/// A small PCM reserve absorbs packet jitter without changing Ray's rate or pitch.
enum SpeechPlayoutPolicy {
    static let maximumScheduledBuffers = 8
    static let primingFrames = IPCSpeechStreamEvent.sampleRate / 10 // 100 ms

    static func shouldStart(
        frames: Int, buffers: Int, providerDone: Bool
    ) -> Bool {
        guard frames > 0, buffers > 0 else { return false }
        // EOF releases short utterances; a full slot queue releases tiny packets
        // so producers cannot deadlock behind a player that has not started.
        return frames >= primingFrames
            || buffers >= maximumScheduledBuffers
            || providerDone
    }
}
