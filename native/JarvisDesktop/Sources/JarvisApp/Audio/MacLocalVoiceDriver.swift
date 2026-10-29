@preconcurrency import AVFoundation
import AppKit
import Foundation
import DesktopCore
@preconcurrency import Speech

@MainActor private final class PermissionRequest: LocalVoiceCancellation {
    var active = true
    func cancel() { active = false }
}

@MainActor private final class RecognitionPreparation: LocalVoiceCancellation {
    var active = true
    var task: Task<Void, Never>?
    func cancel() { active = false; task?.cancel(); task = nil }
    func complete() { active = false; task = nil }
}

/// The tap and teardown can run on different threads. Closing the gate stops
/// appending before endAudio and removes its only retained buffer request.
private final class AudioBufferGate: @unchecked Sendable {
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    init(_ request: SFSpeechAudioBufferRecognitionRequest) { self.request = request }
    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        request?.append(buffer)
    }
    func close() {
        lock.lock(); defer { lock.unlock() }
        request?.endAudio(); request = nil
    }
}

/// No initializer, property getter or cancellation path requests TCC or starts
/// audio. The owner's explicit dictation action is the only authorization entry.
@MainActor public final class MacLocalVoiceDriver: NSObject, LocalVoiceDriver, AVSpeechSynthesizerDelegate {
    private var engine: AVAudioEngine?
    private var tapInstalled = false
    private var gate: AudioBufferGate?
    private var recognizer: SFSpeechRecognizer?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var retiredLegacyTask: SFSpeechRecognitionTask?
    private var recognitionTicket: UUID?
    private var preparationTicket: UUID?
    private var preparation: RecognitionPreparation?
    private var preparedLocale: String?
    private var modernRecognition: (any PreparedLocalRecognition)?
    private var synthesizer: AVSpeechSynthesizer?
    private var utterance: AVSpeechUtterance?
    private var playbackReceiver: (@MainActor (LocalPlaybackEvent) -> Void)?
    private var captureMonitor: LocalCaptureMonitor?
    private var recognitionCleanup: Task<Void, Never>?
    private var cleanupTicket: UUID?

    public override init() { super.init() }

    public static func installedRecognitionLocales() async -> [String] {
        if #available(macOS 26.0, *) {
            guard SpeechTranscriber.isAvailable else { return [] }
            let supported = await SpeechTranscriber.supportedLocales
            guard !Task.isCancelled else { return [] }
            let installed = await SpeechTranscriber.installedLocales
            guard !Task.isCancelled else { return [] }
            return Array(Set(installed.map { $0.identifier(.bcp47) }.filter { requested in
                LocalSpeechPolicy.hasExactInstalledLocale(requested, supported: supported.map { $0.identifier(.bcp47) }, installed: [requested])
            })).sorted()
        }
        return SFSpeechRecognizer.supportedLocales().compactMap { locale in
            let id = locale.identifier
            guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable,
                  recognizer.supportsOnDeviceRecognition else { return nil }
            return id
        }.sorted()
    }

    public func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation {
        let request = PermissionRequest()
        let keys = ["NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription"]
        guard keys.allSatisfy({ key in
            guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String else { return false }
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }) else {
            completion(LocalVoicePermissions(speech: .restricted, microphone: .restricted, configurationMissing: true))
            return request
        }
        SFSpeechRecognizer.requestAuthorization { authorization in
            Task { @MainActor in
                guard request.active else { return }
                let speech = Self.speechPermission(authorization)
                guard speech == .authorized else {
                    completion(LocalVoicePermissions(speech: speech, microphone: .notDetermined)); return
                }
                let microphone = AVCaptureDevice.authorizationStatus(for: .audio)
                guard microphone == .notDetermined else {
                    completion(LocalVoicePermissions(speech: speech, microphone: Self.capturePermission(microphone))); return
                }
                // A stale speech authorization callback must not trigger a new
                // microphone prompt after the owner has cancelled the operation.
                guard request.active else { return }
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    Task { @MainActor in
                        guard request.active else { return }
                        completion(LocalVoicePermissions(speech: speech, microphone: granted ? .authorized : .denied))
                    }
                }
            }
        }
        return request
    }

    public func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation {
        cancelRecognition()
        let request = RecognitionPreparation(), ticket = UUID()
        preparation = request; preparationTicket = ticket
        request.task = Task { @MainActor [weak self, weak request] in
            guard let self, let request else { return }
            let current: @MainActor () -> Bool = { [weak self, weak request] in
                self?.preparationTicket == ticket && request?.active == true && !Task.isCancelled
            }
            do {
                if let retired = self.recognitionCleanup { await retired.value }
                guard current() else { return }
                if let retired = self.retiredLegacyTask {
                    for _ in 0..<40 where retired.state != .completed {
                        guard current() else { return }
                        try await Task.sleep(for: .milliseconds(50))
                    }
                    guard current() else { return }
                    guard retired.state == .completed else { throw LocalVoiceIssue.cleanupUnconfirmed }
                    self.retiredLegacyTask = nil
                }
                guard validLocale(localeIdentifier), LocalSpeechPolicy.canonicalLocale(localeIdentifier) != nil else { throw LocalVoiceIssue.onDeviceUnavailable }
                if #available(macOS 26.0, *) {
                    let ready = try await ModernLocalRecognition.prepare(localeIdentifier: localeIdentifier, isCurrent: current)
                    guard current() else { ready.cancel(); return }
                    self.modernRecognition = ready
                } else {
                    guard let service = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier)),
                          LocalSpeechPolicy.canonicalLocale(service.locale.identifier) == LocalSpeechPolicy.canonicalLocale(localeIdentifier),
                          service.isAvailable, service.supportsOnDeviceRecognition else { throw LocalVoiceIssue.onDeviceUnavailable }
                    guard current() else { return }
                    self.recognizer = service
                }
                guard current() else { return }
                self.preparedLocale = LocalSpeechPolicy.canonicalLocale(localeIdentifier)
                self.preparationTicket = nil; self.preparation = nil
                request.complete()
                completion(.success(()))
            } catch {
                guard current() else { return }
                self.preparationTicket = nil; self.preparation = nil
                request.complete()
                completion(.failure((error as? LocalVoiceIssue) ?? .onDeviceUnavailable))
            }
        }
        return request
    }

    public func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws {
        guard NSApp?.isActive == true else { throw LocalVoiceIssue.interrupted }
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else { throw LocalVoiceIssue.speechDenied }
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { throw LocalVoiceIssue.microphoneDenied }
        guard validLocale(localeIdentifier),
              preparedLocale == LocalSpeechPolicy.canonicalLocale(localeIdentifier) else { throw LocalVoiceIssue.onDeviceUnavailable }
        if let modernRecognition {
            try modernRecognition.start(receive: receive)
            return
        }
        guard let service = recognizer,
              service.isAvailable, service.supportsOnDeviceRecognition else {
            throw LocalVoiceIssue.onDeviceUnavailable
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        let audio = AVAudioEngine()
        let input = audio.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.sampleRate <= 96000,
              format.channelCount > 0, format.channelCount <= 2 else {
            throw LocalVoiceIssue.captureFailed
        }
        let bufferGate = AudioBufferGate(request)
        let ticket = UUID()
        recognitionTicket = ticket; recognizer = service; engine = audio; gate = bufferGate
        recognitionTask = service.recognitionTask(with: request) { [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let final = result?.isFinal ?? false
            let failed = error != nil
            Task { @MainActor [weak self] in
                guard let self, self.recognitionTicket == ticket else { return }
                if let text {
                    receive(final ? .final(text) : .partial(text))
                    if final { return }
                }
                if failed { receive(.failed) }
            }
        }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in bufferGate.append(buffer) }
        tapInstalled = true
        do {
            audio.prepare()
            try audio.start()
        } catch {
            cancelRecognition()
            throw LocalVoiceIssue.captureFailed
        }
        let monitor = LocalCaptureMonitor(engine: audio, permissionIssue: {
            LocalCaptureMonitor.permissionFailure() ?? (audio.isRunning ? nil : .inputChanged)
        }, failed: { [weak self] issue in
            guard self?.recognitionTicket == ticket else { return }
            receive(.issue(issue))
        })
        captureMonitor = monitor; monitor.start()
    }

    public func finishRecognition() {
        if let modernRecognition { modernRecognition.finish(); return }
        stopInput()
        recognitionTask?.finish()
    }

    public func cancelRecognition() {
        preparationTicket = nil
        preparation?.cancel(); preparation = nil; preparedLocale = nil
        if let retired = modernRecognition {
            retired.cancel(); modernRecognition = nil
            let previous = recognitionCleanup, ticket = UUID()
            cleanupTicket = ticket
            recognitionCleanup = Task { @MainActor [weak self] in
                await previous?.value
                await retired.waitForCleanup()
                guard let self, self.cleanupTicket == ticket else { return }
                self.recognitionCleanup = nil; self.cleanupTicket = nil
            }
        }
        recognitionTicket = nil
        stopInput()
        if let retired = recognitionTask { retired.cancel(); retiredLegacyTask = retired }
        recognitionTask = nil
        recognizer = nil
    }

    private func stopInput() {
        captureMonitor?.stop(); captureMonitor = nil
        gate?.close(); gate = nil
        if let engine {
            engine.stop()
            if tapInstalled { engine.inputNode.removeTap(onBus: 0) }
            engine.reset()
        }
        tapInstalled = false; engine = nil
    }

    public func localVoices(localeIdentifier: String) -> [LocalVoiceChoice] {
        guard validLocale(localeIdentifier) else { return [] }
        let language = localeIdentifier.replacingOccurrences(of: "_", with: "-").lowercased()
        let primary = language.split(separator: "-").first
        return AVSpeechSynthesisVoice.speechVoices().compactMap { candidate in
            guard MacLocalVoicePolicy.isAllowed(identifier: candidate.identifier,
                                               isPersonalVoice: candidate.voiceTraits.contains(.isPersonalVoice)),
                  let installed = AVSpeechSynthesisVoice(identifier: candidate.identifier),
                  installed.language.lowercased().split(separator: "-").first == primary else { return nil }
            return LocalVoiceChoice(id: installed.identifier, name: installed.name, language: installed.language)
        }.sorted { first, second in
            let firstExact = first.language.lowercased() == language
            let secondExact = second.language.lowercased() == language
            if firstExact != secondExact { return firstExact }
            return first.id < second.id
        }
    }

    public func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= 16000 else { throw LocalVoiceIssue.textLimit }
        guard MacLocalVoicePolicy.isAllowed(identifier: voice.id, isPersonalVoice: false),
              let installed = AVSpeechSynthesisVoice(identifier: voice.id),
              !installed.voiceTraits.contains(.isPersonalVoice),
              LocalSpeechPolicy.canonicalLocale(installed.language) == LocalSpeechPolicy.canonicalLocale(voice.language) else { throw LocalVoiceIssue.noLocalVoice }
        stopPlayback()
        let player = AVSpeechSynthesizer()
        player.delegate = self
        let speech = AVSpeechUtterance(string: text)
        speech.voice = installed
        speech.rate = AVSpeechUtteranceDefaultSpeechRate
        self.synthesizer = player; self.utterance = speech; playbackReceiver = receive
        player.speak(speech)
    }

    public func stopPlayback() {
        playbackReceiver = nil; utterance = nil
        synthesizer?.stopSpeaking(at: .immediate)
        synthesizer?.delegate = nil; synthesizer = nil
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in self?.complete(utterance, event: .finished) }
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in self?.complete(utterance, event: .cancelled) }
    }

    private func complete(_ utterance: AVSpeechUtterance, event: LocalPlaybackEvent) {
        guard self.utterance === utterance else { return }
        let receiver = playbackReceiver
        stopPlayback()
        receiver?(event)
    }

    private func validLocale(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 64 && value.unicodeScalars.allSatisfy {
            CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_"
        }
    }

    private static func speechPermission(_ value: SFSpeechRecognizerAuthorizationStatus) -> LocalVoicePermission {
        switch value {
        case .authorized: .authorized
        case .denied: .denied
        case .restricted: .restricted
        case .notDetermined: .notDetermined
        @unknown default: .restricted
        }
    }

    private static func capturePermission(_ value: AVAuthorizationStatus) -> LocalVoicePermission {
        switch value {
        case .authorized: .authorized
        case .denied: .denied
        case .restricted: .restricted
        case .notDetermined: .notDetermined
        @unknown default: .restricted
        }
    }
}
