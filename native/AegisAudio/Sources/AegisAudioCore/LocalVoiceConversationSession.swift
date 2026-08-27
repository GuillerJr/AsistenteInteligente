import Foundation

public enum LocalVoiceConversationCommand: Equatable, Sendable {
    case reset

    private static let resetCommands: Set<String> = [
        "comencemos una conversacion nueva",
        "empecemos una conversacion nueva",
        "inicia una conversacion nueva",
        "inicia una nueva conversacion",
        "nueva conversacion",
        "start a new conversation",
    ]

    public static func parse(_ transcript: String) -> Self? {
        resetCommands.contains(LocalVoiceCommandText.normalize(transcript)) ? .reset : nil
    }

    public static let spokenResponse =
        "De acuerdo. La próxima solicitud empezará una conversación nueva."
    public static let unverifiedSpokenResponse =
        "No cambié la conversación porque no pude verificar al propietario."
}

public enum LocalVoiceConversationSession {
    public static let idleTimeout: TimeInterval = 30 * 60

    public struct Decision: Equatable, Sendable {
        public let conversationID: UUID?
        public let persistAcceptedConversation: Bool
        public let boundSpeakerID: String?
        public let discardStoredSession: Bool
    }

    public static func reusableConversationID(
        _ conversationID: UUID?,
        lastUsedAt: Date?,
        now: Date,
        timeout: TimeInterval = idleTimeout
    ) -> UUID? {
        guard
            timeout > 0,
            timeout <= 24 * 60 * 60,
            let conversationID,
            let lastUsedAt
        else {
            return nil
        }
        let idleTime = now.timeIntervalSince(lastUsedAt)
        guard idleTime >= 0, idleTime < timeout else { return nil }
        return conversationID
    }

    public static func decision(
        storedConversationID: UUID?,
        lastUsedAt: Date?,
        storedSpeakerID: String?,
        currentSpeakerID: String?,
        soleSpeakerProfile: Bool,
        speakerIdentityReady: Bool,
        now: Date,
        timeout: TimeInterval = idleTimeout
    ) -> Decision {
        let incompleteState = (storedConversationID == nil) != (lastUsedAt == nil)
            || (storedConversationID == nil && storedSpeakerID != nil)
        let reusableID = incompleteState
            ? nil
            : reusableConversationID(
                storedConversationID,
                lastUsedAt: lastUsedAt,
                now: now,
                timeout: timeout
            )
        let discardStoredSession = incompleteState
            || (storedConversationID != nil && reusableID == nil)

        if speakerIdentityReady {
            guard
                soleSpeakerProfile,
                let currentSpeakerID,
                SpeakerIdentityCapability.isValidSpeakerLabel(currentSpeakerID)
            else {
                return Decision(
                    conversationID: nil,
                    persistAcceptedConversation: false,
                    boundSpeakerID: nil,
                    discardStoredSession: discardStoredSession
                )
            }
            return Decision(
                conversationID: storedSpeakerID == currentSpeakerID ? reusableID : nil,
                persistAcceptedConversation: true,
                boundSpeakerID: currentSpeakerID,
                discardStoredSession: discardStoredSession
            )
        }

        guard storedSpeakerID == nil else {
            return Decision(
                conversationID: nil,
                persistAcceptedConversation: false,
                boundSpeakerID: nil,
                discardStoredSession: discardStoredSession
            )
        }
        return Decision(
            conversationID: reusableID,
            persistAcceptedConversation: true,
            boundSpeakerID: nil,
            discardStoredSession: discardStoredSession
        )
    }
}
