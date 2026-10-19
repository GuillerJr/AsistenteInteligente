import Testing
@testable import AegisAudioCore

@Suite("Natural speech playout")
struct SpeechPlayoutPolicyTests {
    @Test("Accumulates a 100 ms reserve without rate or pitch processing")
    func primingUsesFramesRatherThanPacketCount() {
        #expect(SpeechPlayoutPolicy.primingFrames == 2_205)
        #expect(!SpeechPlayoutPolicy.shouldStart(frames: 2_048, buffers: 1, providerDone: false))
        #expect(SpeechPlayoutPolicy.shouldStart(frames: 2_205, buffers: 2, providerDone: false))
    }

    @Test("EOF releases short utterances without losing their tail")
    func shortUtteranceFlushes() {
        #expect(SpeechPlayoutPolicy.shouldStart(frames: 120, buffers: 1, providerDone: true))
        #expect(!SpeechPlayoutPolicy.shouldStart(frames: 0, buffers: 0, providerDone: true))
    }

    @Test("Tiny packets cannot deadlock the scheduled slot queue")
    func fullSlotsReleasePriming() {
        #expect(SpeechPlayoutPolicy.maximumScheduledBuffers == 8)
        #expect(!SpeechPlayoutPolicy.shouldStart(frames: 7, buffers: 7, providerDone: false))
        #expect(SpeechPlayoutPolicy.shouldStart(frames: 8, buffers: 8, providerDone: false))
    }

    @Test("Invalid counters never start playback")
    func emptyOrInvalidStateStaysSilent() {
        #expect(!SpeechPlayoutPolicy.shouldStart(frames: -1, buffers: 8, providerDone: true))
        #expect(!SpeechPlayoutPolicy.shouldStart(frames: 2_205, buffers: 0, providerDone: true))
    }
}
