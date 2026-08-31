import Testing
@testable import AegisAudioCore

@Suite("Follow-up listening gate")
struct FollowUpListeningGateTests {
    @Test("Starts capture on owner speech inside the five second window")
    func beginsCapture() {
        var gate = FollowUpListeningGate()
        gate.arm(at: 10, voiceIsActive: false)

        #expect(gate.observe(voiceIsActive: true, at: 14.99) == .beginCapture)
        #expect(!gate.isArmed)
    }

    @Test("Rejects playback tail until a quiet transition")
    func rejectsPlaybackTail() {
        var gate = FollowUpListeningGate()
        gate.arm(at: 10, voiceIsActive: true)

        #expect(gate.observe(voiceIsActive: true, at: 10.1) == .none)
        #expect(gate.observe(voiceIsActive: false, at: 10.2) == .none)
        #expect(gate.observe(voiceIsActive: true, at: 10.3) == .beginCapture)
    }

    @Test("Expires at the monotonic deadline")
    func expires() {
        var gate = FollowUpListeningGate()
        gate.arm(at: 10, voiceIsActive: false)

        #expect(gate.expire(at: 14.999) == .none)
        #expect(gate.expire(at: 15) == .expire)
        #expect(!gate.isArmed)
    }
}
