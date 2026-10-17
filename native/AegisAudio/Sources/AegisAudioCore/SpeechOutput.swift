@preconcurrency import AVFoundation
import AudioToolbox
import Foundation
import OSLog

private struct SpeechSegment: Sendable {
    let id = UUID()
    let text: String
}

private struct PrefetchedSpeech: Sendable {
    static let maximumBytes = 2_097_152
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
public final class SpeechOutput: NSObject {
    private static let remoteStartDeadline = Duration.seconds(15)
    private static let prefetchedHandoffDeadline = Duration.seconds(15)
    private static let maximumPendingPCMBufferCount = 16
    private static let maximumScheduledPCMBufferCount = SpeechPlayoutPolicy.maximumScheduledBuffers
    private static let firstChunkPlayoutBudgetMilliseconds = 150
    private let audioEngine = AVAudioEngine()
    private let remotePlayer = AVAudioPlayerNode()
    private let remoteMixer = AVAudioMixerNode()
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "VoiceOutput")
    private var remoteFormat: AVAudioFormat?
    private var remoteTask: Task<Void, Never>?
    private var prefetchTask: Task<PrefetchedSpeech?, Never>?
    private var activePrefetchTask: Task<PrefetchedSpeech?, Never>?
    private var prefetchSegmentID: UUID?
    private var startDeadlineTask: Task<Void, Never>?
    private var fadeGeneration: UUID?
    private var completion: ((SpeechPlaybackOutcome) -> Void)?
    private var streamSecret: Data?
    private var streamGroupToken: String?
    private var queuedSegments: [SpeechSegment] = []
    private var streamFinished = true
    private var segmentActive = false
    private var streamFailure: SpeechPlaybackOutcome?
    private var replayBuffer = SpeechAudioReplayBuffer()
    private var currentAudio: [Data] = []
    private var currentAudioBytes = 0
    private var currentText: [String] = []
    private var currentTextScalarCount = 0
    private var replayCaptureAvailable = true
    private var replaying = false
    private var remoteGeneration: UUID?
    private var remoteProviderDone = false
    private var remotePlaybackStarted = false
    private var scheduledBuffers = 0
    private var primedFrames = 0
    private var pendingRemoteBuffers = PCMBufferRing(
        capacity: SpeechOutput.maximumPendingPCMBufferCount
    )
    private var firstPCMReceivedAt: TimeInterval?

    public override init() {
        super.init()
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

    public var isActive: Bool {
        segmentActive || !queuedSegments.isEmpty || !streamFinished
            || remoteTask != nil || startDeadlineTask != nil
            || remotePlayer.isPlaying || completion != nil
    }

    public var hasAudiblePlayback: Bool {
        remotePlayer.isPlaying
    }

    public func speak(
        _ text: String,
        ipcSecret: Data?,
        completion: @escaping (SpeechPlaybackOutcome) -> Void
    ) {
        beginStream(ipcSecret: ipcSecret, completion: completion)
        enqueue(text)
        finishStream()
    }

    public func replayLocally(
        _ text: String,
        completion: @escaping (SpeechPlaybackOutcome) -> Void
    ) {
        beginStream(ipcSecret: nil, completion: completion)
        streamFinished = true
        guard let chunks = replayBuffer.audio(
            matching: text, at: ProcessInfo.processInfo.systemUptime
        ) else {
            streamFailure = .localReplayUnavailable
            finishAll()
            return
        }
        replaying = true
        segmentActive = true
        let generation = UUID()
        remoteGeneration = generation
        remoteTask = Task { [weak self] in
            guard let self else { return }
            for chunk in chunks {
                guard !Task.isCancelled, remoteGeneration == generation else { return }
                guard await schedulePCM(chunk, generation: generation) else {
                    remoteStreamFailed(generation: generation, text: "")
                    return
                }
            }
            remoteStreamFinished(generation: generation)
        }
    }

    public func clearReplay() {
        replayBuffer.clear()
        currentAudio.removeAll(keepingCapacity: false)
        replayCaptureAvailable = false
    }

    public func beginStream(
        ipcSecret: Data?, completion: @escaping (SpeechPlaybackOutcome) -> Void
    ) {
        stop()
        streamSecret = ipcSecret
        streamGroupToken = ipcSecret == nil ? nil : Self.makeStreamGroupToken()
        streamFinished = false
        streamFailure = nil
        self.completion = completion
        logger.info("voice_stream_started")
    }

    public func enqueue(_ text: String) {
        guard !streamFinished, streamFailure == nil else { return }
        let normalized = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard !normalized.isEmpty else { return }
        let additionalScalars = normalized.unicodeScalars.count + (currentText.isEmpty ? 0 : 1)
        guard normalized.utf8.count <= 8_192, normalized.unicodeScalars.count <= 2_000,
              queuedSegments.count < 64,
              currentTextScalarCount + additionalScalars <= 2_000
        else {
            failStream(.invalidText)
            return
        }
        currentText.append(normalized)
        currentTextScalarCount += additionalScalars
        queuedSegments.append(SpeechSegment(text: normalized))
        playNextIfNeeded()
        prefetchNextIfPossible()
    }

    public func finishStream() {
        if !replaying, currentText.isEmpty, streamFailure == nil {
            streamFailure = .invalidText
        }
        streamFinished = true
        playNextIfNeeded()
    }

    public func stop() {
        fadeGeneration = nil
        cancelRemoteSpeechStreams()
        remoteTask?.cancel()
        remoteTask = nil
        cancelPrefetch()
        startDeadlineTask?.cancel()
        startDeadlineTask = nil
        resetRemotePlayback(stopEngine: true)
        remoteMixer.outputVolume = 0.96
        queuedSegments.removeAll(keepingCapacity: false)
        streamSecret = nil
        streamFinished = true
        segmentActive = false
        streamFailure = nil
        currentAudio.removeAll(keepingCapacity: false)
        currentText.removeAll(keepingCapacity: false)
        currentTextScalarCount = 0
        currentAudioBytes = 0
        replayCaptureAvailable = true
        replaying = false
        completion = nil
    }

    /// Cancels generation immediately, then removes audible Magpie output with
    /// a 150 ms linear ramp before clearing any scheduled PCM buffers.
    public func interruptWithFade() async {
        let remoteWasAudible = audioEngine.isRunning && remotePlayer.isPlaying
        // Invalidate callbacks before awaiting the fade: old completions must not
        // schedule another segment or refill the private replay buffer.
        remoteGeneration = nil
        cancelRemoteSpeechStreams()
        remoteTask?.cancel()
        remoteTask = nil
        cancelPrefetch()
        startDeadlineTask?.cancel()
        startDeadlineTask = nil
        completion = nil
        streamSecret = nil
        streamFinished = true
        segmentActive = false
        streamFailure = nil
        currentAudio.removeAll(keepingCapacity: false)
        currentText.removeAll(keepingCapacity: false)
        currentTextScalarCount = 0
        currentAudioBytes = 0
        queuedSegments.removeAll(keepingCapacity: false)

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

    private func playNextIfNeeded() {
        guard !segmentActive else { return }
        guard !queuedSegments.isEmpty else {
            if streamFinished { finishAll() }
            return
        }
        segmentActive = true
        let segment = queuedSegments.removeFirst()
        let text = segment.text
        guard let streamSecret, let streamGroupToken else {
            failStream(.unavailable)
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
        primedFrames = 0
        pendingRemoteBuffers.removeAll()
        firstPCMReceivedAt = nil
        logger.info("voice_synthesis_requested provider=nvidia_magpie mode=stream")

        armStartDeadline(
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
        primedFrames = 0
        pendingRemoteBuffers.removeAll()
        firstPCMReceivedAt = nil
        activePrefetchTask = task
        logger.info("voice_synthesis_requested provider=nvidia_magpie mode=prefetched_stream")
        armStartDeadline(
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

    private func armStartDeadline(
        deadline: Duration,
        generation: UUID,
        text: String
    ) {
        startDeadlineTask = Task { [weak self] in
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
            startDeadlineTask = nil
            logger.error("voice_output_unavailable reason=start_deadline")
            failStream(.unavailable)
        }
    }

    private func prefetchNextIfPossible() {
        guard
            segmentActive,
            remotePlaybackStarted,
            streamFailure == nil,
            !replaying,
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
            streamFailure == nil
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
        if !replaying, replayCaptureAvailable {
            currentAudioBytes += pcm.count
            if currentAudioBytes <= SpeechAudioReplayBuffer.maximumBytes,
               currentAudio.count < SpeechAudioReplayBuffer.maximumChunks {
                currentAudio.append(pcm)
            } else {
                replayCaptureAvailable = false
                currentAudio.removeAll(keepingCapacity: false)
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
            if !remotePlaybackStarted { primedFrames += Int(buffer.frameLength) }
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
        guard remotePlaybackStarted || SpeechPlayoutPolicy.shouldStart(
            frames: primedFrames, buffers: scheduledBuffers, providerDone: remoteProviderDone
        ) else { return }
        if !remotePlayer.isPlaying {
            remotePlayer.play()
        }
        if !remotePlaybackStarted {
            remotePlaybackStarted = true
            startDeadlineTask?.cancel()
            startDeadlineTask = nil
            let elapsedMilliseconds = firstPCMReceivedAt.map {
                Int(((ProcessInfo.processInfo.systemUptime - $0) * 1_000).rounded())
            } ?? -1
            logger.info(
                "voice_playback_started source=nvidia_magpie mode=stream first_pcm_to_play_ms=\(elapsedMilliseconds, privacy: .public) budget_met=\(elapsedMilliseconds >= 0 && elapsedMilliseconds <= Self.firstChunkPlayoutBudgetMilliseconds, privacy: .public)"
            )
            prefetchNextIfPossible()
        }
    }

    private func remoteStreamFinished(generation: UUID) {
        guard remoteGeneration == generation else { return }
        remoteTask = nil
        activePrefetchTask = nil
        startDeadlineTask?.cancel()
        startDeadlineTask = nil
        remoteProviderDone = true
        drainRemoteBufferQueue(generation: generation)
        guard remotePlaybackStarted else {
            failStream(.unavailable)
            return
        }
        if scheduledBuffers == 0, pendingRemoteBuffers.isEmpty {
            segmentDidFinish()
        } else {
            prefetchNextIfPossible()
        }
    }

    private func remoteStreamFailed(generation: UUID, text: String) {
        guard remoteGeneration == generation else { return }
        failStream(remotePlaybackStarted ? .interrupted : .unavailable)
    }

    private func remoteBufferFinished(generation: UUID) {
        guard remoteGeneration == generation else { return }
        scheduledBuffers = max(0, scheduledBuffers - 1)
        drainRemoteBufferQueue(generation: generation)
        if remoteProviderDone, scheduledBuffers == 0, pendingRemoteBuffers.isEmpty {
            segmentDidFinish()
        }
    }

    private func failStream(_ outcome: SpeechPlaybackOutcome) {
        streamFailure = outcome
        remoteTask?.cancel()
        remoteTask = nil
        startDeadlineTask?.cancel()
        startDeadlineTask = nil
        cancelRemoteSpeechStreams()
        cancelPrefetch()
        resetRemotePlayback(stopEngine: true)
        queuedSegments.removeAll(keepingCapacity: false)
        currentAudio.removeAll(keepingCapacity: false)
        replayCaptureAvailable = false
        segmentActive = false
        if streamFinished { finishAll() }
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
        primedFrames = 0
        pendingRemoteBuffers.removeAll()
        firstPCMReceivedAt = nil
        remotePlayer.stop()
        if stopEngine {
            audioEngine.stop()
        }
    }

    private func segmentDidFinish() {
        resetRemotePlayback(stopEngine: false)
        segmentActive = false
        playNextIfNeeded()
    }

    private func finishAll() {
        guard completion != nil else { return }
        resetRemotePlayback(stopEngine: true)
        let outcome = streamFailure ?? .completed
        if !replaying, outcome == .completed, replayCaptureAvailable {
            replayBuffer.store(
                text: currentText.joined(separator: " "), chunks: currentAudio,
                at: ProcessInfo.processInfo.systemUptime
            )
        }
        currentAudio.removeAll(keepingCapacity: false)
        currentText.removeAll(keepingCapacity: false)
        currentTextScalarCount = 0
        currentAudioBytes = 0
        logger.info("voice_stream_finished result=\(outcome.failureCode ?? "completed", privacy: .public)")
        let callback = completion
        completion = nil
        streamSecret = nil
        streamGroupToken = nil
        callback?(outcome)
    }
}
