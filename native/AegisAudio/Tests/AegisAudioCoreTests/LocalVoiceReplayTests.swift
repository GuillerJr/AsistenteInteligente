import Testing
@testable import AegisAudioCore

@Suite("Local voice replay")
struct LocalVoiceReplayTests {
    @Test("Parses only exact replay commands")
    func parsesExactCommands() {
        #expect(LocalVoiceReplayCommand.parse("¡Repítelo!") == .replay)
        #expect(LocalVoiceReplayCommand.parse("Dilo otra vez") == .replay)
        #expect(LocalVoiceReplayCommand.parse("Repite el correo de ayer") == nil)
        #expect(LocalVoiceReplayCommand.parse("Otra vez revisa mi calendario") == nil)
    }

    @Test("Normalizes and bounds one ephemeral response")
    func storesBoundedResponse() {
        var buffer = LocalVoiceReplayBuffer()
        let response = "  Hola\n\t" + String(repeating: "a", count: 2_500)

        let accepted = buffer.store(response, at: 10)
        let stored = buffer.response(at: 11)

        #expect(accepted)
        #expect(stored?.hasPrefix("Hola ") == true)
        #expect(stored?.count == 2_000)
    }

    @Test("Expires without polling and rejects a reversed clock")
    func expiresAndRejectsClockRollback() {
        var buffer = LocalVoiceReplayBuffer()
        let accepted = buffer.store("Respuesta", at: 100)
        let recent = buffer.response(
            at: 100 + LocalVoiceReplayBuffer.retention - 0.01
        )
        let expired = buffer.response(at: 100 + LocalVoiceReplayBuffer.retention)

        let acceptedAgain = buffer.store("Otra respuesta", at: 200)
        let reversed = buffer.response(at: 199)

        #expect(accepted)
        #expect(recent != nil)
        #expect(expired == nil)
        #expect(acceptedAgain)
        #expect(reversed == nil)
    }

    @Test("Clears invalid and explicit state")
    func clearsState() {
        var buffer = LocalVoiceReplayBuffer()
        let rejected = buffer.store("   ", at: 10)
        let empty = buffer.response(at: 10)

        let accepted = buffer.store("Respuesta", at: 20)
        buffer.clear()
        let cleared = buffer.response(at: 21)

        #expect(!rejected)
        #expect(empty == nil)
        #expect(accepted)
        #expect(cleared == nil)
    }
}
