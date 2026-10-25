@preconcurrency import AVFoundation
import DesktopCore
import Foundation
@preconcurrency import Speech

@MainActor protocol PreparedLocalRecognition: AnyObject {
    func start(receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws
    func finish()
    func cancel()
}

/// Each prepared session owns its analyzer, stream, tasks and (only after start)
/// its engine. A delayed cancellation never reaches another session's resources.
@available(macOS 26.0, *)
@MainActor final class ModernLocalRecognition: PreparedLocalRecognition {
    private let transcriber: SpeechTranscriber
    private let analyzer: SpeechAnalyzer
    private let format: AVAudioFormat
    private let continuation: AsyncStream<AnalyzerInput>.Continuation
    private var active = true
    private var finishing = false
    private var engine: AVAudioEngine?
    private var tapInstalled = false
    private var input: ModernPCMInput?
    private var lastInput: ModernPCMInput?
    private var resultTask: Task<Void, Never>?
    private var finishTask: Task<Void, Never>?
    private var receiver: (@MainActor (LocalRecognitionEvent) -> Void)?
    private var segments = LocalSpeechSegments()

    private init(transcriber: SpeechTranscriber, analyzer: SpeechAnalyzer, format: AVAudioFormat,
                 continuation: AsyncStream<AnalyzerInput>.Continuation) {
        self.transcriber = transcriber; self.analyzer = analyzer
        self.format = format; self.continuation = continuation
    }

    static func prepare(localeIdentifier: String, isCurrent: @escaping @MainActor () -> Bool) async throws -> ModernLocalRecognition {
        guard isCurrent(), SpeechTranscriber.isAvailable else { throw LocalVoiceIssue.onDeviceUnavailable }
        let supported = await SpeechTranscriber.supportedLocales
        guard isCurrent() else { throw CancellationError() }
        let installed = await SpeechTranscriber.installedLocales
        guard isCurrent() else { throw CancellationError() }
        guard LocalSpeechPolicy.hasExactInstalledLocale(localeIdentifier,
            supported: supported.map { $0.identifier(.bcp47) }, installed: installed.map { $0.identifier(.bcp47) }),
              let locale = supported.first(where: {
                  LocalSpeechPolicy.canonicalLocale($0.identifier(.bcp47)) == LocalSpeechPolicy.canonicalLocale(localeIdentifier)
              }) else { throw LocalVoiceIssue.onDeviceUnavailable }
        let transcriber = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)
        // There is deliberately no reserve/release/download API in this adapter.
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            guard isCurrent() else { throw CancellationError() }
            throw LocalVoiceIssue.onDeviceUnavailable
        }
        guard isCurrent() else { throw CancellationError() }
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .bufferingOldest(LocalSpeechPolicy.queuedAudioBuffers))
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let prepared = ModernLocalRecognition(transcriber: transcriber, analyzer: analyzer, format: format, continuation: continuation)
        do {
            try await analyzer.prepareToAnalyze(in: format)
            guard isCurrent() else { throw CancellationError() }
            // Module status can be `supported` for a model already installed by
            // another app. Readiness comes from the exact installed catalog and
            // actual preparation, never from reserving or downloading an asset.
            let currentSupported = await SpeechTranscriber.supportedLocales
            guard isCurrent() else { throw CancellationError() }
            let currentInstalled = await SpeechTranscriber.installedLocales
            guard isCurrent() else { throw CancellationError() }
            guard LocalSpeechPolicy.hasExactInstalledLocale(localeIdentifier,
                supported: currentSupported.map { $0.identifier(.bcp47) }, installed: currentInstalled.map { $0.identifier(.bcp47) }) else {
                throw LocalVoiceIssue.onDeviceUnavailable
            }
            try await analyzer.start(inputSequence: stream)
            guard isCurrent() else { throw CancellationError() }
            return prepared
        } catch {
            prepared.cancel()
            throw error
        }
    }

    func start(receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws {
        guard active, engine == nil, !finishing else { throw LocalVoiceIssue.captureFailed }
        let audio = AVAudioEngine()
        let inputNode = audio.inputNode
        let sourceFormat = inputNode.outputFormat(forBus: 0)
        let gate = try ModernPCMInput(source: sourceFormat, destination: format, continuation: continuation) { [weak self] in
            Task { @MainActor [weak self] in
                guard let self, self.active else { return }
                self.receiver?(.issue(.captureFailed))
            }
        }
        receiver = receive; engine = audio; input = gate; lastInput = gate
        let transcriber = transcriber
        resultTask = Task { @MainActor [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self, self.active, !Task.isCancelled else { return }
                    guard self.lastInput?.hasFailed != true else { self.receiver?(.issue(.captureFailed)); return }
                    let text = try self.segments.accept(text: String(result.text.characters),
                        start: result.range.start.seconds, end: result.range.end.seconds, isFinal: result.isFinal)
                    // Final means this segment is stable, not that the input
                    // session ended. Publish only after the result stream drains.
                    self.receiver?(.partial(text))
                }
                guard let self, self.active, !Task.isCancelled else { return }
                guard self.lastInput?.hasFailed != true else { self.receiver?(.issue(.captureFailed)); return }
                self.receiver?(self.finishing ? .final(self.segments.text) : .failed)
            } catch {
                guard let self, self.active, !Task.isCancelled else { return }
                self.receiver?(.issue((error as? LocalVoiceIssue) ?? .recognitionFailed))
            }
        }
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: sourceFormat) { buffer, _ in gate.append(buffer) }
        tapInstalled = true
        do { audio.prepare(); try audio.start() }
        catch { cancel(); throw LocalVoiceIssue.captureFailed }
    }

    func finish() {
        guard active, !finishing else { return }
        finishing = true
        stopInput(finalize: true)
        let analyzer = analyzer
        finishTask = Task { @MainActor [weak self] in
            do {
                try await analyzer.finalizeAndFinishThroughEndOfInput()
                guard let self, self.active, !Task.isCancelled else { return }
                // The result task observes stream exhaustion after all final
                // segments. The core still enforces its original 3 s deadline.
                await self.resultTask?.value
            } catch {
                guard let self, self.active, !Task.isCancelled else { return }
                self.receiver?(.failed)
            }
        }
    }

    func cancel() {
        guard active else { return }
        active = false; receiver = nil
        stopInput(finalize: false)
        continuation.finish()
        lastInput = nil
        resultTask?.cancel(); resultTask = nil
        finishTask?.cancel(); finishTask = nil
        let analyzer = analyzer
        // This closure retains only the old analyzer, never a driver or engine.
        Task { await analyzer.cancelAndFinishNow() }
    }

    private func stopInput(finalize: Bool) {
        if finalize { input?.finish() } else { input?.cancel() }
        input = nil
        if let engine {
            engine.stop()
            if tapInstalled { engine.inputNode.removeTap(onBus: 0) }
            engine.reset()
        }
        tapInstalled = false; engine = nil
    }
}

/// The tap's buffer is borrowed from AVAudioEngine. Copy it before the callback
/// returns, convert synchronously, and queue at most eight <=64 KiB owned buffers.
/// Overflow is terminal and visible; it never drops audio while claiming success.
@available(macOS 26.0, *)
private final class ModernPCMInput: @unchecked Sendable {
    private let lock = NSLock()
    private let converter: AVAudioConverter
    private let destination: AVAudioFormat
    private let continuation: AsyncStream<AnalyzerInput>.Continuation
    private let failed: @Sendable () -> Void
    private var active = true
    private var failure = false

    var hasFailed: Bool {
        lock.lock(); defer { lock.unlock() }
        return failure
    }

    init(source: AVAudioFormat, destination: AVAudioFormat,
         continuation: AsyncStream<AnalyzerInput>.Continuation, failed: @escaping @Sendable () -> Void) throws {
        guard [source, destination].allSatisfy({ format in
            format.sampleRate.isFinite && format.sampleRate > 0 && format.sampleRate <= 96_000
                && (1...2).contains(format.channelCount)
                && [.pcmFormatFloat32, .pcmFormatInt16, .pcmFormatInt32].contains(format.commonFormat)
        }), let converter = AVAudioConverter(from: source, to: destination) else { throw LocalVoiceIssue.captureFailed }
        self.converter = converter; self.destination = destination
        self.continuation = continuation; self.failed = failed
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard active else { return }
        do {
            let owned = try Self.copy(buffer)
            try convert(owned)
        } catch { failLocked() }
    }

    private static func copy(_ buffer: AVAudioPCMBuffer) throws -> AVAudioPCMBuffer {
        let source = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        let bytes = source.reduce(0) { $0 + Int($1.mDataByteSize) }
        guard LocalSpeechPolicy.acceptsPCM(sampleRate: buffer.format.sampleRate, channels: buffer.format.channelCount,
                                          frames: buffer.frameLength, bytes: bytes),
              let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else { throw LocalVoiceIssue.captureFailed }
        copy.frameLength = buffer.frameLength
        let target = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
        guard source.count == target.count else { throw LocalVoiceIssue.captureFailed }
        for index in source.indices {
            guard source[index].mDataByteSize <= target[index].mDataByteSize,
                  let from = source[index].mData, let to = target[index].mData else { throw LocalVoiceIssue.captureFailed }
            memcpy(to, from, Int(source[index].mDataByteSize))
        }
        return copy
    }

    private func convert(_ buffer: AVAudioPCMBuffer?) throws {
        let capacity = buffer.map { ceil(Double($0.frameLength) * destination.sampleRate / $0.format.sampleRate) + 32 } ?? 8192
        guard capacity > 0, capacity <= 8192,
              let output = AVAudioPCMBuffer(pcmFormat: destination, frameCapacity: AVAudioFrameCount(capacity)) else { throw LocalVoiceIssue.captureFailed }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, state in
            if let buffer, !supplied { supplied = true; state.pointee = .haveData; return buffer }
            state.pointee = buffer == nil ? .endOfStream : .noDataNow
            return nil
        }
        guard error == nil, status != .error else { throw LocalVoiceIssue.captureFailed }
        if output.frameLength == 0 { return }
        // This is an owned converter output, never the engine's reusable buffer.
        let bytes = UnsafeMutableAudioBufferListPointer(output.mutableAudioBufferList).reduce(0) { $0 + Int($1.mDataByteSize) }
        guard LocalSpeechPolicy.acceptsPCM(sampleRate: destination.sampleRate, channels: destination.channelCount,
                                          frames: output.frameLength, bytes: bytes) else { throw LocalVoiceIssue.captureFailed }
        switch continuation.yield(AnalyzerInput(buffer: output)) {
        case .enqueued: break
        case .dropped, .terminated: throw LocalVoiceIssue.captureFailed
        @unknown default: throw LocalVoiceIssue.captureFailed
        }
    }

    func finish() {
        lock.lock(); defer { lock.unlock() }
        guard active else { return }
        do { try convert(nil) } catch { failLocked(); return }
        active = false; continuation.finish()
    }

    func cancel() {
        lock.lock(); defer { lock.unlock() }
        active = false; continuation.finish()
    }

    private func failLocked() {
        guard active else { return }
        active = false; failure = true; continuation.finish(); failed()
    }
}
