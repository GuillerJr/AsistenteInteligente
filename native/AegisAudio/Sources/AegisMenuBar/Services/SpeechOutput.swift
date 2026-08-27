import AegisAudioCore
@preconcurrency import AVFoundation
import Foundation
import OSLog

private struct SpeechSegment: Sendable {
    let text: String
}

@MainActor
final class SpeechOutput: NSObject, AVSpeechSynthesizerDelegate {
    private static let remoteStartDeadline = Duration.milliseconds(1_800)
    private static let artificialVoiceNames = Set([
        "eddy", "flo", "grandma", "grandpa", "reed", "rocko", "sandy", "shelley",
    ])

    private let synthesizer = AVSpeechSynthesizer()
    private let audioEngine = AVAudioEngine()
    private let remotePlayer = AVAudioPlayerNode()
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "VoiceOutput")
    private var remoteFormat: AVAudioFormat?
    private var remoteTask: Task<Void, Never>?
    private var latencyFallbackTask: Task<Void, Never>?
    private var fallbackUtterance: AVSpeechUtterance?
    private var completion: (() -> Void)?
    private var streamSecret: Data?
    private var queuedSegments: [SpeechSegment] = []
    private var streamFinished = true
    private var segmentActive = false
    private var fallbackOnlyForStream = false
    private var remoteGeneration: UUID?
    private var remoteProviderDone = false
    private var remotePlaybackStarted = false
    private var scheduledBuffers = 0

    override init() {
        super.init()
        synthesizer.delegate = self
        remotePlayer.volume = 0.96
        audioEngine.attach(remotePlayer)
        if let format = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: Double(IPCSpeechStreamEvent.sampleRate),
            channels: 1,
            interleaved: false
        ) {
            audioEngine.connect(remotePlayer, to: audioEngine.mainMixerNode, format: format)
            remoteFormat = format
        }
    }

    var isActive: Bool {
        segmentActive || !queuedSegments.isEmpty || !streamFinished
            || remoteTask != nil || latencyFallbackTask != nil
            || remotePlayer.isPlaying || synthesizer.isSpeaking || completion != nil
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
        queuedSegments.append(SpeechSegment(text: String(normalized.prefix(2_000))))
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
        resetRemotePlayback(stopEngine: true)
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

    private func playNextIfNeeded() {
        guard !segmentActive else { return }
        guard !queuedSegments.isEmpty else {
            if streamFinished { finishAll() }
            return
        }
        segmentActive = true
        let text = queuedSegments.removeFirst().text
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
        startRemoteSpeech(text, secret: streamSecret)
    }

    private func startRemoteSpeech(_ text: String, secret: Data) {
        let generation = UUID()
        remoteGeneration = generation
        remoteProviderDone = false
        remotePlaybackStarted = false
        scheduledBuffers = 0
        logger.info("voice_synthesis_requested provider=nvidia_magpie mode=stream")

        latencyFallbackTask = Task { [weak self] in
            do {
                try await Task.sleep(for: Self.remoteStartDeadline)
            } catch {
                return
            }
            guard
                let self,
                !Task.isCancelled,
                remoteGeneration == generation,
                !remotePlaybackStarted
            else { return }
            latencyFallbackTask = nil
            remoteTask?.cancel()
            remoteTask = nil
            enterFallbackMode()
            resetRemotePlayback(stopEngine: true)
            logger.info("voice_fallback reason=latency_budget")
            speakFallback(text)
        }

        remoteTask = Task.detached(priority: .userInitiated) { [weak self] in
            guard let client = try? LocalIPCClient(secret: secret) else {
                await self?.remoteStreamFailed(generation: generation, text: text)
                return
            }
            var token: String?
            defer {
                if let token {
                    _ = try? client.closeSpeechStream(token)
                }
            }
            guard
                !Task.isCancelled,
                let opened = try? client.openSpeechStream(text),
                var event = IPCSpeechStreamEvent(response: opened)
            else {
                if !Task.isCancelled {
                    await self?.remoteStreamFailed(generation: generation, text: text)
                }
                return
            }
            token = event.token
            while !Task.isCancelled {
                guard await self?.acceptRemote(event, generation: generation, text: text) == true
                else { return }
                if event.done {
                    await self?.remoteStreamFinished(generation: generation)
                    return
                }
                guard
                    let response = try? client.nextSpeechStream(
                        token: event.token,
                        afterSequence: event.sequence
                    ),
                    let next = IPCSpeechStreamEvent(response: response),
                    next.token == event.token,
                    next.sequence == event.sequence + 1
                else {
                    if !Task.isCancelled {
                        await self?.remoteStreamFailed(generation: generation, text: text)
                    }
                    return
                }
                event = next
            }
        }
    }

    private func acceptRemote(
        _ event: IPCSpeechStreamEvent,
        generation: UUID,
        text: String
    ) -> Bool {
        guard
            remoteGeneration == generation,
            segmentActive,
            !fallbackOnlyForStream
        else { return false }
        guard event.pcm.isEmpty || schedulePCM(event.pcm, generation: generation) else {
            remoteStreamFailed(generation: generation, text: text)
            return false
        }
        return true
    }

    private func schedulePCM(_ pcm: Data, generation: UUID) -> Bool {
        guard
            let remoteFormat,
            let buffer = AVAudioPCMBuffer(
                pcmFormat: remoteFormat,
                frameCapacity: AVAudioFrameCount(pcm.count / 2)
            ),
            let destination = buffer.int16ChannelData?[0]
        else { return false }
        buffer.frameLength = buffer.frameCapacity
        pcm.withUnsafeBytes { source in
            if let address = source.baseAddress {
                memcpy(destination, address, pcm.count)
            }
        }
        do {
            if !audioEngine.isRunning {
                audioEngine.prepare()
                try audioEngine.start()
            }
        } catch {
            return false
        }
        scheduledBuffers += 1
        remotePlayer.scheduleBuffer(
            buffer,
            completionCallbackType: .dataPlayedBack
        ) { [weak self] _ in
            Task { @MainActor in
                self?.remoteBufferFinished(generation: generation)
            }
        }
        if !remotePlayer.isPlaying {
            remotePlayer.play()
        }
        if !remotePlaybackStarted {
            remotePlaybackStarted = true
            latencyFallbackTask?.cancel()
            latencyFallbackTask = nil
            logger.info("voice_playback_started source=nvidia_magpie mode=stream")
        }
        return true
    }

    private func remoteStreamFinished(generation: UUID) {
        guard remoteGeneration == generation else { return }
        remoteTask = nil
        latencyFallbackTask?.cancel()
        latencyFallbackTask = nil
        remoteProviderDone = true
        if scheduledBuffers == 0 {
            segmentDidFinish()
        }
    }

    private func remoteStreamFailed(generation: UUID, text: String) {
        guard remoteGeneration == generation else { return }
        remoteTask = nil
        latencyFallbackTask?.cancel()
        latencyFallbackTask = nil
        enterFallbackMode()
        if remotePlaybackStarted {
            remoteProviderDone = true
            logger.error("voice_stream_interrupted source=nvidia_magpie")
            if scheduledBuffers == 0 {
                segmentDidFinish()
            }
        } else {
            resetRemotePlayback(stopEngine: true)
            logger.info("voice_fallback reason=provider_unavailable")
            speakFallback(text)
        }
    }

    private func remoteBufferFinished(generation: UUID) {
        guard remoteGeneration == generation else { return }
        scheduledBuffers = max(0, scheduledBuffers - 1)
        if remoteProviderDone, scheduledBuffers == 0 {
            segmentDidFinish()
        }
    }

    private func enterFallbackMode() {
        fallbackOnlyForStream = true
    }

    private func resetRemotePlayback(stopEngine: Bool) {
        remoteGeneration = nil
        remoteProviderDone = false
        remotePlaybackStarted = false
        scheduledBuffers = 0
        remotePlayer.stop()
        if stopEngine {
            audioEngine.stop()
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
        resetRemotePlayback(stopEngine: false)
        segmentActive = false
        playNextIfNeeded()
    }

    private func finishAll() {
        guard completion != nil else { return }
        resetRemotePlayback(stopEngine: true)
        logger.info("voice_stream_completed")
        let callback = completion
        completion = nil
        streamSecret = nil
        callback?()
    }
}
