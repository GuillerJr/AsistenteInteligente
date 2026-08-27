import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Local voice conversation session")
struct LocalVoiceConversationSessionTests {
    @Test("Parses only exact reset commands")
    func resetCommands() {
        for transcript in [
            "Nueva conversación",
            "Jarvis, empecemos una conversación nueva",
            "Inicia una nueva conversación",
            "Start a new conversation",
        ] {
            #expect(LocalVoiceConversationCommand.parse(transcript) == .reset)
        }
        for transcript in [
            "Nueva conversación sobre seguridad",
            "Resume nuestra conversación",
            "Borra la conversación anterior",
            "Inicia una conversación y abre Safari",
        ] {
            #expect(LocalVoiceConversationCommand.parse(transcript) == nil)
        }
    }

    @Test("Reuses only a recent valid session")
    func recentSession() {
        let identifier = UUID()
        let now = Date(timeIntervalSince1970: 1_800_000_000)

        #expect(
            LocalVoiceConversationSession.reusableConversationID(
                identifier,
                lastUsedAt: now.addingTimeInterval(-1_799),
                now: now
            ) == identifier
        )
        #expect(
            LocalVoiceConversationSession.reusableConversationID(
                identifier,
                lastUsedAt: now.addingTimeInterval(-1_800),
                now: now
            ) == nil
        )
        #expect(
            LocalVoiceConversationSession.reusableConversationID(
                identifier,
                lastUsedAt: now.addingTimeInterval(1),
                now: now
            ) == nil
        )
    }

    @Test("Fails closed for incomplete or invalid state")
    func invalidState() {
        let identifier = UUID()
        let now = Date()

        #expect(
            LocalVoiceConversationSession.reusableConversationID(
                nil,
                lastUsedAt: now,
                now: now
            ) == nil
        )
        #expect(
            LocalVoiceConversationSession.reusableConversationID(
                identifier,
                lastUsedAt: nil,
                now: now
            ) == nil
        )
        #expect(
            LocalVoiceConversationSession.reusableConversationID(
                identifier,
                lastUsedAt: now,
                now: now,
                timeout: 0
            ) == nil
        )
    }
}
