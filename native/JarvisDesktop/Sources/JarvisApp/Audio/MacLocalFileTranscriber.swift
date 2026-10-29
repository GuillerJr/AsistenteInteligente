@preconcurrency import AVFoundation
import DesktopCore
import Foundation
@preconcurrency import Speech

@available(macOS 26.0, *)
private struct FileAnalyzerInputs: AsyncSequence, Sendable {
    typealias Element = AnalyzerInput
    struct AsyncIterator: AsyncIteratorProtocol {
        var input: MemoryMediaAudioSequence.AsyncIterator
        mutating func next() async throws -> AnalyzerInput? {
            try Task.checkCancellation()
            guard SFSpeechRecognizer.authorizationStatus() == .authorized else { throw LocalFileSpeechIssue.authorizationRequired }
            guard let buffer = try await input.next() else { return nil }
            return AnalyzerInput(buffer: buffer)
        }
    }
    let buffers: MemoryMediaAudioSequence
    func makeAsyncIterator() -> AsyncIterator { AsyncIterator(input: buffers.makeAsyncIterator()) }
}

@available(macOS 26.0, *)
@MainActor private final class LocalFileAnalysisOwner {
    let analyzer: SpeechAnalyzer
    let stream: MemoryMediaAudioStream
    let results: Task<String, Error>
    private var cleanup: Task<Void, Never>?
    init(analyzer: SpeechAnalyzer, stream: MemoryMediaAudioStream, results: Task<String, Error>) {
        self.analyzer = analyzer; self.stream = stream; self.results = results
    }
    func close() async {
        if cleanup == nil {
            let analyzer = analyzer, stream = stream, results = results
            results.cancel()
            cleanup = Task {
                await analyzer.cancelAndFinishNow()
                await stream.close()
                _ = await results.result
            }
        }
        await cleanup?.value
    }
}

/// Approved audio bytes remain in RAM. The decoder reads only a custom local
/// resource; there are no temporary files, network URLs, microphone access,
/// automatic asset downloads, or alternate inference providers.
@MainActor public final class MacLocalFileTranscriber {
    public init() {}

    /// Invoke only after the exact media operation was approved. Permission is
    /// inspected, never requested from a decoder or background worker.
    public func transcribe(data: Data, name: String, mimeType: String, localeIdentifier: String = "es-ES",
                           expectedSHA256: String) async throws -> LocalAudioTranscript {
        try LocalAudioTranscriptionPolicy.validateSource(data: data, name: name, mimeType: mimeType, expectedSHA256: expectedSHA256)
        guard #available(macOS 26.0, *) else { throw LocalFileSpeechIssue.unsupportedPlatform }
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else { throw LocalFileSpeechIssue.authorizationRequired }
        return try await withThrowingTaskGroup(of: LocalAudioTranscript.self) { group in
            group.addTask { @MainActor in
                try await Self.recognize(data: data, mimeType: mimeType, localeIdentifier: localeIdentifier, sourceSHA256: expectedSHA256)
            }
            group.addTask {
                try await Task.sleep(for: .seconds(LocalAudioTranscriptionPolicy.maximumProcessingSeconds))
                throw LocalFileSpeechIssue.deadline
            }
            do {
                guard let value = try await group.next() else { throw LocalFileSpeechIssue.recognitionFailed }
                group.cancelAll()
                return value
            } catch {
                group.cancelAll()
                if error is CancellationError { throw CancellationError() }
                if let issue = error as? LocalFileSpeechIssue { throw issue }
                if error as? LocalVoiceIssue == .textLimit { throw LocalFileSpeechIssue.textLimit }
                // Framework diagnostics can contain source metadata. Return a
                // stable public code rather than an NSError description.
                throw LocalFileSpeechIssue.recognitionFailed
            }
        }
    }

    /// A visible owner action may call this method. It requests Speech only;
    /// importing/transcribing an existing file never requests microphone TCC.
    public static func requestSpeechPermission() async -> LocalVoicePermission {
        guard let purpose = Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") as? String,
              !purpose.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return .restricted }
        return await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                switch status {
                case .authorized: continuation.resume(returning: .authorized)
                case .denied: continuation.resume(returning: .denied)
                case .restricted: continuation.resume(returning: .restricted)
                case .notDetermined: continuation.resume(returning: .notDetermined)
                @unknown default: continuation.resume(returning: .restricted)
                }
            }
        }
    }

    @available(macOS 26.0, *)
    private static func recognize(data: Data, mimeType: String, localeIdentifier: String, sourceSHA256: String) async throws -> LocalAudioTranscript {
        try Task.checkCancellation()
        guard SpeechTranscriber.isAvailable else { throw LocalFileSpeechIssue.localeUnavailable }
        let supported = await SpeechTranscriber.supportedLocales
        try Task.checkCancellation()
        let installed = await SpeechTranscriber.installedLocales
        try Task.checkCancellation()
        guard LocalSpeechPolicy.hasExactInstalledLocale(localeIdentifier,
            supported: supported.map { $0.identifier(.bcp47) }, installed: installed.map { $0.identifier(.bcp47) }),
              let locale = supported.first(where: {
                  LocalSpeechPolicy.canonicalLocale($0.identifier(.bcp47)) == LocalSpeechPolicy.canonicalLocale(localeIdentifier)
              }) else { throw LocalFileSpeechIssue.localeUnavailable }
        let transcriber = SpeechTranscriber(locale: locale, preset: .transcription)
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw LocalFileSpeechIssue.localeUnavailable
        }
        try Task.checkCancellation()
        let source = try MemoryMediaAsset(data: data, mimeType: mimeType)
        let stream: MemoryMediaAudioStream
        do {
            stream = try await withTaskCancellationHandler {
                try await source.audioBuffers(format: format, maxDuration: LocalAudioTranscriptionPolicy.maximumDuration)
            } onCancel: { source.close() }
            try LocalAudioTranscriptionPolicy.validateDuration(stream.duration)
        } catch { source.close(); throw error }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let results = Task { @MainActor in
            var segments = LocalSpeechSegments(maximumBytes: LocalAudioTranscriptionPolicy.maximumTextBytes)
            for try await result in transcriber.results {
                try Task.checkCancellation()
                _ = try segments.accept(text: String(result.text.characters), start: result.range.start.seconds,
                                        end: result.range.end.seconds, isFinal: result.isFinal)
            }
            guard !segments.hasUnfinalizedText else { throw LocalFileSpeechIssue.recognitionFailed }
            return segments.finalizedText.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let owner = LocalFileAnalysisOwner(analyzer: analyzer, stream: stream, results: results)
        do {
            let text = try await withTaskCancellationHandler {
                try await analyzer.prepareToAnalyze(in: format)
                try Task.checkCancellation()
                guard SFSpeechRecognizer.authorizationStatus() == .authorized else { throw LocalFileSpeechIssue.authorizationRequired }
                let currentInstalled = await SpeechTranscriber.installedLocales
                try Task.checkCancellation()
                guard LocalSpeechPolicy.hasExactInstalledLocale(localeIdentifier,
                    supported: supported.map { $0.identifier(.bcp47) }, installed: currentInstalled.map { $0.identifier(.bcp47) }) else {
                    throw LocalFileSpeechIssue.localeUnavailable
                }
                let last = try await analyzer.analyzeSequence(FileAnalyzerInputs(buffers: stream.buffers))
                try Task.checkCancellation()
                guard let last else { throw LocalFileSpeechIssue.recognitionFailed }
                try await analyzer.finalizeAndFinish(through: last)
                let text = try await results.value
                try Task.checkCancellation()
                guard SFSpeechRecognizer.authorizationStatus() == .authorized else { throw LocalFileSpeechIssue.authorizationRequired }
                return text
            } onCancel: {
                Task { @MainActor in await owner.close() }
            }
            await owner.close()
            return try LocalAudioTranscript(text: text, localeIdentifier: localeIdentifier,
                                            durationSeconds: stream.duration, sourceSHA256: sourceSHA256)
        } catch {
            await owner.close()
            throw error
        }
    }
}
