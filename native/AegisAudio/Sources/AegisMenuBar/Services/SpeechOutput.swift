import AegisAudioCore
@preconcurrency import AVFoundation
import AudioToolbox
import Foundation
import OSLog

private struct SpeechSegment: Sendable {
    let id = UUID()
    let text: String
}

private struct PrefetchedSpeech: Sendable {
    static let maximumBytes = 1_048_576
    static let maximumChunks = maximumBytes / IPCSpeechStreamEvent.maximumPCMBytes

    let segmentID: UUID
    let pcmChunks: [Data]
}

private struct PCMBufferRing {
    private var storage: [AVAudioPCMBuffer?]
    private var readIndex = 0
    private var writeIndex = 0
    private(set) var count = 0

    init(capacity: Int) {
        precondition(capacity > 0)
        storage = Array(repeating: nil, count: capacity)
    }

    var isEmpty: Bool { count == 0 }

    mutating func enqueue(_ buffer: AVAudioPCMBuffer) -> Bool {
        guard count < storage.count else { return false }
        storage[writeIndex] = buffer
        writeIndex = (writeIndex + 1) % storage.count
        count += 1
        return true
    }

    mutating func dequeue() -> AVAudioPCMBuffer? {
        guard count > 0 else { return nil }
        let buffer = storage[readIndex]
        storage[readIndex] = nil
        readIndex = (readIndex + 1) % storage.count
        count -= 1
        return buffer
    }

    mutating func removeAll() {
        for index in storage.indices {
            storage[index] = nil
        }
        readIndex = 0
        writeIndex = 0
        count = 0
    }
}

@MainActor
final class SpeechOutput: NSObject, AVSpeechSynthesizerDelegate {
    private static let remoteStartDeadline = Duration.milliseconds(1_800)
    private static let prefetchedHandoffDeadline = Duration.milliseconds(350)
    private static let maximumPendingPCMBufferCount = 16
    private static let maximumScheduledPCMBufferCount = 8
    private static let firstChunkPlayoutBudgetMilliseconds = 150
    private static let artificialVoiceNames = Set([
        "eddy", "flo", "grandma", "grandpa", "reed", "rocko", "sandy", "shelley",
    ])

    private let synthesizer = AVSpeechSynthesizer()
    private let audioEngine = AVAudioEngine()
    private let remotePlayer = AVAudioPlayerNode()
    private let remoteMixer = AVAudioMixerNode()
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "VoiceOutput")
    private var remoteFormat: AVAudioFormat?
    private var remoteTask: Task<Void, Never>?
    private var prefetchTask: Task<PrefetchedSpeech?, Never>?
    private var activePrefetchTask: Task<PrefetchedSpeech?, Never>?
    private var prefetchSegmentID: UUID?
    private var latencyFallbackTask: Task<Void, Never>?
    private var fadeGeneration: UUID?
    private var fallbackUtterance: AVSpeechUtterance?
    private var completion: (() -> Void)?
    private var streamSecret: Data?
    private var streamGroupToken: String?
    private var queuedSegments: [SpeechSegment] = []
    private var streamFinished = true
    private var segmentActive = false
    private var fallbackOnlyForStream = false
    private var remoteGeneration: UUID?
    private var remoteProviderDone = false
    private var remotePlaybackStarted = false
    private var scheduledBuffers = 0
    private var pendingRemoteBuffers = PCMBufferRing(
        capacity: SpeechOutput.maximumPendingPCMBufferCount
    )
    private var firstPCMReceivedAt: TimeInterval?

    override init() {
        super.init()
        synthesizer.delegate = self
        remotePlayer.volume = 1
        remoteMixer.outputVolume = 0.96
        audioEngine.attach(remotePlayer)
        audioEngine.attach(remoteMixer)
        if let format = AVAudioFormat(
            standardFormatWithSampleRate: Double(IPCSpeechStreamEvent.sampleRate),
            channels: 1
        ) {
            audioEngine.connect(remotePlayer, to: remoteMixer, format: format)
            // AVAudioMixerNode accepts canonical non-interleaved Float32. Its
            // output connection negotiates the hardware sample rate and channel
            // count, so 22.05 kHz mono Magpie audio is converted by Core Audio.
            audioEngine.connect(remoteMixer, to: audioEngine.mainMixerNode, format: nil)
            remoteFormat = format
        }
    }

    var isActive: Bool {
        segmentActive || !queuedSegments.isEmpty || !streamFinished
            || remoteTask != nil || latencyFallbackTask != nil
            || remotePlayer.isPlaying || synthesizer.isSpeaking || completion != nil
    }

    var hasAudiblePlayback: Bool {
        remotePlayer.isPlaying || synthesizer.isSpeaking
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

    func speakLocally(
        _ text: String,
        completion: @escaping () -> Void
    ) {
        beginStream(ipcSecret: nil, completion: completion)
        fallbackOnlyForStream = true
        logger.info("voice_synthesis_requested provider=apple mode=local_only")
        enqueue(text)
        finishStream()
    }

    func beginStream(ipcSecret: Data?, completion: @escaping () -> Void) {
        stop()
        streamSecret = ipcSecret
        streamGroupToken = ipcSecret == nil ? nil : Self.makeStreamGroupToken()
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
        prefetchNextIfPossible()
    }

    func finishStream() {
        streamFinished = true
        playNextIfNeeded()
    }

    func stop() {
        fadeGeneration = nil
        cancelRemoteSpeechStreams()
        remoteTask?.cancel()
        remoteTask = nil
        cancelPrefetch()
        latencyFallbackTask?.cancel()
        latencyFallbackTask = nil
        fallbackUtterance = nil
        synthesizer.stopSpeaking(at: .immediate)
        resetRemotePlayback(stopEngine: true)
        remoteMixer.outputVolume = 0.96
        queuedSegments.removeAll(keepingCapacity: false)
        streamSecret = nil
        streamFinished = true
        segmentActive = false
        fallbackOnlyForStream = false
        completion = nil
    }

    /// Cancels generation immediately, then removes audible Magpie output with
    /// a 150 ms linear ramp before clearing any scheduled PCM buffers.
    func interruptWithFade() async {
        let remoteWasAudible = audioEngine.isRunning && remotePlayer.isPlaying
        cancelRemoteSpeechStreams()
        remoteTask?.cancel()
        remoteTask = nil
        cancelPrefetch()
        latencyFallbackTask?.cancel()
        latencyFallbackTask = nil
        completion = nil
        streamSecret = nil
        streamFinished = true
        segmentActive = false
        fallbackOnlyForStream = false
        queuedSegments.removeAll(keepingCapacity: false)

        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .word)
            fallbackUtterance = nil
        }
        guard remoteWasAudible else {
            resetRemotePlayback(stopEngine: true)
            remoteMixer.outputVolume = 0.96
            return
        }

        let fadeID = UUID()
        fadeGeneration = fadeID
        let rampFrames = AVAudioFrameCount(
            (remoteFormat?.sampleRate ?? Double(IPCSpeechStreamEvent.sampleRate)) * 0.150
        )
        logger.info("voice_playback_fade_started duration_ms=150")
        remoteMixer.auAudioUnit.scheduleParameterBlock(
            AUEventSampleTimeImmediate,
            rampFrames,
            AUParameterAddress(kMultiChannelMixerParam_Volume),
            0
        )
        // The cleanup must still run if the parent voice-turn task is cancelled;
        // otherwise the mixer could remain silent with queued PCM still alive.
        let fadeDelay = Task.detached(priority: .userInitiated) {
            try? await Task.sleep(for: .milliseconds(150))
        }
        await fadeDelay.value
        guard fadeGeneration == fadeID else { return }
        fadeGeneration = nil
        resetRemotePlayback(stopEngine: true)
        remoteMixer.outputVolume = 0.96
        logger.info("voice_playback_fade_completed duration_ms=150")
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
        let segment = queuedSegments.removeFirst()
        let text = segment.text
        if fallbackOnlyForStream {
            speakFallback(text)
            return
        }
        guard let streamSecret, let streamGroupToken else {
            fallbackOnlyForStream = true
            logger.info("voice_fallback reason=ipc_unavailable")
            speakFallback(text)
            return
        }
        if let prefetched = takePrefetch(for: segment.id) {
            startPrefetchedSpeech(segment, task: prefetched)
            return
        }
        startRemoteSpeech(text, secret: streamSecret, groupToken: streamGroupToken)
    }

    private func startRemoteSpeech(
        _ text: String,
        secret: Data,
        groupToken: String
    ) {
        let generation = UUID()
        remoteGeneration = generation
        remoteProviderDone = false
        remotePlaybackStarted = false
        scheduledBuffers = 0
        pendingRemoteBuffers.removeAll()
        firstPCMReceivedAt = nil
        logger.info("voice_synthesis_requested provider=nvidia_magpie mode=stream")

        armLatencyFallback(
            deadline: Self.remoteStartDeadline,
            generation: generation,
            text: text
        )

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
                let opened = try? client.openSpeechStream(text, groupToken: groupToken),
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

    private func startPrefetchedSpeech(
        _ segment: SpeechSegment,
        task: Task<PrefetchedSpeech?, Never>
    ) {
        let generation = UUID()
        remoteGeneration = generation
        remoteProviderDone = false
        remotePlaybackStarted = false
        scheduledBuffers = 0
        pendingRemoteBuffers.removeAll()
        firstPCMReceivedAt = nil
        activePrefetchTask = task
        logger.info("voice_synthesis_requested provider=nvidia_magpie mode=prefetched_stream")
        armLatencyFallback(
            deadline: Self.prefetchedHandoffDeadline,
            generation: generation,
            text: segment.text
        )
        remoteTask = Task { [weak self] in
            let prefetched = await task.value
            guard
                let self,
                !Task.isCancelled,
                remoteGeneration == generation
            else { return }
            activePrefetchTask = nil
            guard
                let prefetched,
                prefetched.segmentID == segment.id,
                !prefetched.pcmChunks.isEmpty
            else {
                remoteStreamFailed(generation: generation, text: segment.text)
                return
            }
            for pcm in prefetched.pcmChunks {
                guard await schedulePCM(pcm, generation: generation) else {
                    remoteStreamFailed(generation: generation, text: segment.text)
                    return
                }
            }
            remoteStreamFinished(generation: generation)
        }
    }

    private func armLatencyFallback(
        deadline: Duration,
        generation: UUID,
        text: String
    ) {
        latencyFallbackTask = Task { [weak self] in
            do {
                try await Task.sleep(for: deadline)
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
    }

    private func prefetchNextIfPossible() {
        guard
            segmentActive,
            remotePlaybackStarted,
            remoteProviderDone,
            !fallbackOnlyForStream,
            prefetchTask == nil,
            activePrefetchTask == nil,
            let streamSecret,
            let streamGroupToken,
            let next = queuedSegments.first
        else { return }
        prefetchSegmentID = next.id
        prefetchTask = Task.detached(priority: .utility) {
            Self.fetchPrefetchedSpeech(
                next,
                secret: streamSecret,
                groupToken: streamGroupToken
            )
        }
        logger.debug("voice_synthesis_prefetched mode=stream")
    }

    private func takePrefetch(
        for segmentID: UUID
    ) -> Task<PrefetchedSpeech?, Never>? {
        guard prefetchSegmentID == segmentID else {
            cancelPrefetch()
            return nil
        }
        let task = prefetchTask
        prefetchTask = nil
        prefetchSegmentID = nil
        return task
    }

    private func cancelPrefetch() {
        prefetchTask?.cancel()
        prefetchTask = nil
        activePrefetchTask?.cancel()
        activePrefetchTask = nil
        prefetchSegmentID = nil
    }

    private nonisolated static func fetchPrefetchedSpeech(
        _ segment: SpeechSegment,
        secret: Data,
        groupToken: String
    ) -> PrefetchedSpeech? {
        guard
            !currentTaskIsCancelled,
            let client = try? LocalIPCClient(secret: secret)
        else { return nil }
        var token: String?
        defer {
            if let token {
                _ = try? client.closeSpeechStream(token)
            }
        }
        guard
            let opened = try? client.openSpeechStream(
                segment.text,
                groupToken: groupToken
            ),
            var event = IPCSpeechStreamEvent(response: opened)
        else { return nil }
        token = event.token
        var chunks: [Data] = []
        var totalBytes = 0
        while !currentTaskIsCancelled {
            if !event.pcm.isEmpty {
                totalBytes += event.pcm.count
                guard
                    totalBytes <= PrefetchedSpeech.maximumBytes,
                    chunks.count < PrefetchedSpeech.maximumChunks
                else { return nil }
                chunks.append(event.pcm)
            }
            if event.done {
                return chunks.isEmpty
                    ? nil
                    : PrefetchedSpeech(segmentID: segment.id, pcmChunks: chunks)
            }
            guard
                let response = try? client.nextSpeechStream(
                    token: event.token,
                    afterSequence: event.sequence
                ),
                let next = IPCSpeechStreamEvent(response: response),
                next.token == event.token,
                next.sequence == event.sequence + 1
            else { return nil }
            event = next
        }
        return nil
    }

    private nonisolated static var currentTaskIsCancelled: Bool {
        withUnsafeCurrentTask { $0?.isCancelled ?? false }
    }

    private func acceptRemote(
        _ event: IPCSpeechStreamEvent,
        generation: UUID,
        text: String
    ) async -> Bool {
        guard
            remoteGeneration == generation,
            segmentActive,
            !fallbackOnlyForStream
        else { return false }
        if !event.pcm.isEmpty {
            if firstPCMReceivedAt == nil {
                firstPCMReceivedAt = ProcessInfo.processInfo.systemUptime
            }
            guard await schedulePCM(event.pcm, generation: generation) else {
                remoteStreamFailed(generation: generation, text: text)
                return false
            }
        }
        return true
    }

    private func schedulePCM(_ pcm: Data, generation: UUID) async -> Bool {
        if firstPCMReceivedAt == nil {
            firstPCMReceivedAt = ProcessInfo.processInfo.systemUptime
        }
        guard
            !pcm.isEmpty,
            pcm.count <= IPCSpeechStreamEvent.maximumPCMBytes,
            pcm.count.isMultiple(of: 2),
            let remoteFormat,
            let buffer = AVAudioPCMBuffer(
                pcmFormat: remoteFormat,
                frameCapacity: AVAudioFrameCount(pcm.count / 2)
            ),
            let destination = buffer.floatChannelData?[0]
        else { return false }
        buffer.frameLength = buffer.frameCapacity
        pcm.withUnsafeBytes { source in
            let sampleCount = pcm.count / MemoryLayout<Int16>.size
            for index in 0..<sampleCount {
                let encoded = source.loadUnaligned(
                    fromByteOffset: index * MemoryLayout<Int16>.size,
                    as: UInt16.self
                )
                let sample = Int16(bitPattern: UInt16(littleEndian: encoded))
                destination[index] = Float(sample) / 32_768
            }
        }
        while pendingRemoteBuffers.count >= Self.maximumPendingPCMBufferCount {
            guard remoteGeneration == generation, !Task.isCancelled else { return false }
            do {
                try await Task.sleep(for: .milliseconds(5))
            } catch {
                return false
            }
        }
        guard remoteGeneration == generation else { return false }
        guard pendingRemoteBuffers.enqueue(buffer) else { return false }
        do {
            if !audioEngine.isRunning {
                audioEngine.prepare()
                try audioEngine.start()
            }
        } catch {
            return false
        }
        drainRemoteBufferQueue(generation: generation)
        return true
    }

    private func drainRemoteBufferQueue(generation: UUID) {
        guard remoteGeneration == generation else { return }
        while
            scheduledBuffers < Self.maximumScheduledPCMBufferCount,
            !pendingRemoteBuffers.isEmpty
        {
            guard let buffer = pendingRemoteBuffers.dequeue() else { break }
            scheduledBuffers += 1
            remotePlayer.scheduleBuffer(
                buffer,
                completionCallbackType: .dataPlayedBack
            ) { [weak self] _ in
                Task { @MainActor in
                    self?.remoteBufferFinished(generation: generation)
                }
            }
        }
        guard scheduledBuffers > 0 else { return }
        if !remotePlayer.isPlaying {
            remotePlayer.play()
        }
        if !remotePlaybackStarted {
            remotePlaybackStarted = true
            latencyFallbackTask?.cancel()
            latencyFallbackTask = nil
            let elapsedMilliseconds = firstPCMReceivedAt.map {
                Int(((ProcessInfo.processInfo.systemUptime - $0) * 1_000).rounded())
            } ?? -1
            logger.info(
                "voice_playback_started source=nvidia_magpie mode=stream first_pcm_to_play_ms=\(elapsedMilliseconds, privacy: .public) budget_met=\(elapsedMilliseconds >= 0 && elapsedMilliseconds <= Self.firstChunkPlayoutBudgetMilliseconds, privacy: .public)"
            )
        }
    }

    private func remoteStreamFinished(generation: UUID) {
        guard remoteGeneration == generation else { return }
        remoteTask = nil
        activePrefetchTask = nil
        latencyFallbackTask?.cancel()
        latencyFallbackTask = nil
        remoteProviderDone = true
        if scheduledBuffers == 0, pendingRemoteBuffers.isEmpty {
            segmentDidFinish()
        } else {
            prefetchNextIfPossible()
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
            if scheduledBuffers == 0, pendingRemoteBuffers.isEmpty {
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
        drainRemoteBufferQueue(generation: generation)
        if remoteProviderDone, scheduledBuffers == 0, pendingRemoteBuffers.isEmpty {
            segmentDidFinish()
        }
    }

    private func enterFallbackMode() {
        fallbackOnlyForStream = true
        cancelRemoteSpeechStreams()
        cancelPrefetch()
    }

    private func cancelRemoteSpeechStreams() {
        guard let secret = streamSecret, let groupToken = streamGroupToken else {
            streamGroupToken = nil
            return
        }
        streamGroupToken = nil
        Task.detached(priority: .userInitiated) {
            guard let client = try? LocalIPCClient(secret: secret) else { return }
            _ = try? client.cancelSpeechStreams(groupToken: groupToken)
        }
    }

    private nonisolated static func makeStreamGroupToken() -> String {
        UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
    }

    private func resetRemotePlayback(stopEngine: Bool) {
        remoteGeneration = nil
        remoteProviderDone = false
        remotePlaybackStarted = false
        scheduledBuffers = 0
        pendingRemoteBuffers.removeAll()
        firstPCMReceivedAt = nil
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
        streamGroupToken = nil
        callback?()
    }
}
