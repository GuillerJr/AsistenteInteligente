import AegisAudioCore
@preconcurrency import AVFoundation
import Foundation
import OSLog

@MainActor
final class SpeechOutput: NSObject, AVAudioPlayerDelegate, AVSpeechSynthesizerDelegate {
    private static let remoteStartDeadline = Duration.milliseconds(1_800)
    private static let artificialVoiceNames = Set([
        "eddy", "flo", "grandma", "grandpa", "reed", "rocko", "sandy", "shelley"
    ])
    private let synthesizer = AVSpeechSynthesizer()
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "VoiceOutput")
    private var remoteTask: Task<Void, Never>?
    private var latencyFallbackTask: Task<Void, Never>?
    private var audioPlayer: AVAudioPlayer?
    private var fallbackUtterance: AVSpeechUtterance?
    private var completion: (() -> Void)?
    private var streamSecret: Data?
    private var queuedSegments: [String] = []
    private var streamFinished = true
    private var segmentActive = false
    private var fallbackOnlyForStream = false

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    var isActive: Bool {
        segmentActive || !queuedSegments.isEmpty || !streamFinished
            || remoteTask != nil || latencyFallbackTask != nil
            || audioPlayer?.isPlaying == true || synthesizer.isSpeaking || completion != nil
    }

    func speak(
        _ text: String,
        ipcSecret: Data?,
        completion: @escaping () -> Void
    ) {
        beginStream(ipcSecret: ipcSecret, completion: completion)
        enqueue(text)
        finishStream()
    }

    func beginStream(ipcSecret: Data?, completion: @escaping () -> Void) {
        stop()
        streamSecret = ipcSecret
        streamFinished = false
        fallbackOnlyForStream = false
        self.completion = completion
        logger.info("voice_stream_started")
    }

    func enqueue(_ text: String) {
        let normalized = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard !normalized.isEmpty, normalized.utf8.count <= 8_192 else { return }
        queuedSegments.append(String(normalized.prefix(2_000)))
        playNextIfNeeded()
    }

    func finishStream() {
        streamFinished = true
        playNextIfNeeded()
    }

    func stop() {
        remoteTask?.cancel()
        remoteTask = nil
        latencyFallbackTask?.cancel()
        latencyFallbackTask = nil
        fallbackUtterance = nil
        synthesizer.stopSpeaking(at: .immediate)
        audioPlayer?.delegate = nil
        audioPlayer?.stop()
        audioPlayer = nil
        queuedSegments.removeAll(keepingCapacity: false)
        streamSecret = nil
        streamFinished = true
        segmentActive = false
        fallbackOnlyForStream = false
        completion = nil
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didFinish utterance: AVSpeechUtterance
    ) {
        let utteranceIdentifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard self?.fallbackUtterance.map(ObjectIdentifier.init) == utteranceIdentifier else {
                return
            }
            self?.fallbackUtterance = nil
            self?.segmentDidFinish()
        }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didCancel utterance: AVSpeechUtterance
    ) {
        let utteranceIdentifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard self?.fallbackUtterance.map(ObjectIdentifier.init) == utteranceIdentifier else {
                return
            }
            self?.fallbackUtterance = nil
            self?.segmentDidFinish()
        }
    }

    nonisolated func audioPlayerDidFinishPlaying(
        _ player: AVAudioPlayer,
        successfully flag: Bool
    ) {
        Task { @MainActor [weak self] in
            guard self?.audioPlayer === player else { return }
            self?.audioPlayer = nil
            self?.segmentDidFinish()
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(
        _ player: AVAudioPlayer,
        error: (any Error)?
    ) {
        Task { @MainActor [weak self] in
            guard self?.audioPlayer === player else { return }
            self?.logger.error("voice_playback_failed source=remote")
            self?.audioPlayer = nil
            self?.segmentDidFinish()
        }
    }

    private func playNextIfNeeded() {
        guard !segmentActive else { return }
        guard !queuedSegments.isEmpty else {
            if streamFinished { finishAll() }
            return
        }
        segmentActive = true
        let text = queuedSegments.removeFirst()
        if fallbackOnlyForStream {
            speakFallback(text)
            return
        }
        guard let streamSecret else {
            fallbackOnlyForStream = true
            logger.info("voice_fallback reason=ipc_unavailable")
            speakFallback(text)
            return
        }
        logger.info("voice_synthesis_requested provider=nvidia_magpie")
        latencyFallbackTask = Task { [weak self] in
            do {
                try await Task.sleep(for: Self.remoteStartDeadline)
            } catch {
                return
            }
            guard let self, !Task.isCancelled, remoteTask != nil else { return }
            latencyFallbackTask = nil
            remoteTask?.cancel()
            remoteTask = nil
            fallbackOnlyForStream = true
            logger.info("voice_fallback reason=latency_budget")
            speakFallback(text)
        }
        remoteTask = Task { [weak self, text, streamSecret] in
            let data = await Task.detached(priority: .userInitiated) {
                Self.fetchRemoteSpeech(text, secret: streamSecret)
            }.value
            guard let self, !Task.isCancelled else { return }
            remoteTask = nil
            latencyFallbackTask?.cancel()
            latencyFallbackTask = nil
            guard let data else {
                fallbackOnlyForStream = true
                logger.info("voice_fallback reason=provider_unavailable")
                speakFallback(text)
                return
            }
            guard playRemote(data) else {
                fallbackOnlyForStream = true
                logger.error("voice_playback_failed source=remote")
                speakFallback(text)
                return
            }
            logger.info("voice_playback_started source=nvidia_magpie")
        }
    }

    private nonisolated static func fetchRemoteSpeech(_ text: String, secret: Data) -> Data? {
        guard
            let client = try? LocalIPCClient(secret: secret),
            let response = try? client.synthesizeSpeech(text),
            let artifact = IPCSpeechArtifactEvent(response: response)
        else {
            return nil
        }
        defer { _ = try? client.releaseSpeechArtifact(artifact.token) }
        guard let reader = try? SpeechArtifactReader() else { return nil }
        return try? reader.read(artifact)
    }

    private func playRemote(_ data: Data) -> Bool {
        do {
            let player = try AVAudioPlayer(data: data)
            player.delegate = self
            player.enableRate = true
            player.rate = 1.0
            player.volume = 0.96
            audioPlayer = player
            guard player.prepareToPlay(), player.play() else {
                player.delegate = nil
                audioPlayer = nil
                return false
            }
            return true
        } catch {
            return false
        }
    }

    private func speakFallback(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        let voice = Self.bestFallbackVoice()
        utterance.voice = voice
        utterance.rate = voice?.quality == .premium ? 0.51 : 0.49
        utterance.pitchMultiplier = 0.98
        utterance.volume = 0.96
        utterance.preUtteranceDelay = 0.02
        utterance.postUtteranceDelay = 0.04
        fallbackUtterance = utterance
        synthesizer.speak(utterance)
    }

    private static func bestFallbackVoice() -> AVSpeechSynthesisVoice? {
        AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.hasPrefix("es-") }
            .max { fallbackScore($0) < fallbackScore($1) }
            ?? AVSpeechSynthesisVoice(language: "es-US")
    }

    private static func fallbackScore(_ voice: AVSpeechSynthesisVoice) -> Int {
        if artificialVoiceNames.contains(voice.name.lowercased()) { return -1_000 }
        let quality = switch voice.quality {
        case .premium: 40
        case .enhanced: 20
        default: 0
        }
        let naturalVoice = switch voice.name.lowercased() {
        case "paulina": 60
        case "mónica", "monica": 50
        default: 0
        }
        let gender = voice.gender == .male ? 8 : 0
        let locale = switch voice.language {
        case "es-US": 6
        case "es-MX": 5
        case "es-ES": 4
        default: 0
        }
        return naturalVoice + quality + gender + locale
    }

    private func segmentDidFinish() {
        segmentActive = false
        playNextIfNeeded()
    }

    private func finishAll() {
        guard completion != nil else { return }
        logger.info("voice_stream_completed")
        let callback = completion
        completion = nil
        streamSecret = nil
        callback?()
    }
}
