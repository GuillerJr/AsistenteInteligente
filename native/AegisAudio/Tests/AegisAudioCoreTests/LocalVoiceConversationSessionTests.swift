import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Local voice conversation session")
struct LocalVoiceConversationSessionTests {
    private let modelA = String(repeating: "a", count: 64)
    private let modelB = String(repeating: "b", count: 64)

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
        #expect(LocalVoiceConversationCommand.spokenResponse.contains("conversación nueva"))
        #expect(
            LocalVoiceConversationCommand.unverifiedSpokenResponse.contains(
                "verificar al propietario"
            )
        )
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

    @Test("Reuses context only for the bound owner speaker")
    func boundSpeaker() {
        let identifier = UUID()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let matching = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: now.addingTimeInterval(-60),
            storedSpeakerID: "owner",
            storedModelFingerprint: modelA,
            currentSpeakerID: "owner",
            currentModelFingerprint: modelA,
            ownerSpeakerProfile: true,
            speakerIdentityReady: true,
            now: now
        )
        let replacement = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: now.addingTimeInterval(-60),
            storedSpeakerID: "previous-owner",
            storedModelFingerprint: modelA,
            currentSpeakerID: "owner",
            currentModelFingerprint: modelA,
            ownerSpeakerProfile: true,
            speakerIdentityReady: true,
            now: now
        )

        #expect(matching.conversationID == identifier)
        #expect(matching.persistAcceptedConversation)
        #expect(matching.boundSpeakerID == "owner")
        #expect(matching.boundModelFingerprint == modelA)
        #expect(!matching.discardStoredSession)
        #expect(replacement.conversationID == nil)
        #expect(replacement.persistAcceptedConversation)
        #expect(replacement.boundSpeakerID == "owner")
        #expect(replacement.boundModelFingerprint == modelA)
    }

    @Test("Does not transfer private context to a replacement model")
    func replacementModel() {
        let identifier = UUID()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let decision = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: now.addingTimeInterval(-60),
            storedSpeakerID: "owner",
            storedModelFingerprint: modelA,
            currentSpeakerID: "owner",
            currentModelFingerprint: modelB,
            ownerSpeakerProfile: true,
            speakerIdentityReady: true,
            now: now
        )

        #expect(decision.conversationID == nil)
        #expect(decision.persistAcceptedConversation)
        #expect(decision.boundSpeakerID == "owner")
        #expect(decision.boundModelFingerprint == modelB)
    }

    @Test("Isolates unverified voice without replacing owner context")
    func unverifiedSpeaker() {
        let identifier = UUID()
        let now = Date(timeIntervalSince1970: 1_800_000_000)

        for decision in [
            LocalVoiceConversationSession.decision(
                storedConversationID: identifier,
                lastUsedAt: now.addingTimeInterval(-60),
                storedSpeakerID: "owner",
                storedModelFingerprint: modelA,
                currentSpeakerID: nil,
                currentModelFingerprint: modelA,
                ownerSpeakerProfile: false,
                speakerIdentityReady: true,
                now: now
            ),
            LocalVoiceConversationSession.decision(
                storedConversationID: identifier,
                lastUsedAt: now.addingTimeInterval(-60),
                storedSpeakerID: "owner",
                storedModelFingerprint: modelA,
                currentSpeakerID: "guest",
                currentModelFingerprint: modelA,
                ownerSpeakerProfile: false,
                speakerIdentityReady: true,
                now: now
            ),
        ] {
            #expect(decision.conversationID == nil)
            #expect(!decision.persistAcceptedConversation)
            #expect(decision.boundSpeakerID == nil)
            #expect(decision.boundModelFingerprint == nil)
            #expect(!decision.discardStoredSession)
        }
    }

    @Test("Protects a bound session when identity becomes unavailable")
    func unavailableIdentity() {
        let identifier = UUID()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let protected = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: now.addingTimeInterval(-60),
            storedSpeakerID: "owner",
            storedModelFingerprint: modelA,
            currentSpeakerID: nil,
            currentModelFingerprint: nil,
            ownerSpeakerProfile: false,
            speakerIdentityReady: false,
            now: now
        )
        let legacy = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: now.addingTimeInterval(-60),
            storedSpeakerID: nil,
            storedModelFingerprint: nil,
            currentSpeakerID: nil,
            currentModelFingerprint: nil,
            ownerSpeakerProfile: false,
            speakerIdentityReady: false,
            now: now
        )

        #expect(protected.conversationID == nil)
        #expect(!protected.persistAcceptedConversation)
        #expect(legacy.conversationID == identifier)
        #expect(legacy.persistAcceptedConversation)
    }

    @Test("Discards expired or incomplete stored state")
    func discardState() {
        let identifier = UUID()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let expired = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: now.addingTimeInterval(-1_800),
            storedSpeakerID: "owner",
            storedModelFingerprint: modelA,
            currentSpeakerID: nil,
            currentModelFingerprint: modelA,
            ownerSpeakerProfile: false,
            speakerIdentityReady: true,
            now: now
        )
        let incomplete = LocalVoiceConversationSession.decision(
            storedConversationID: identifier,
            lastUsedAt: nil,
            storedSpeakerID: "owner",
            storedModelFingerprint: modelA,
            currentSpeakerID: "owner",
            currentModelFingerprint: modelA,
            ownerSpeakerProfile: true,
            speakerIdentityReady: true,
            now: now
        )

        #expect(expired.discardStoredSession)
        #expect(incomplete.discardStoredSession)
        #expect(incomplete.conversationID == nil)
        #expect(incomplete.persistAcceptedConversation)
    }
}
