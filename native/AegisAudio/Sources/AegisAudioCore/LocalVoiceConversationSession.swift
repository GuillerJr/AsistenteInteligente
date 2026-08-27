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
}

public enum LocalVoiceConversationSession {
    public static let idleTimeout: TimeInterval = 30 * 60

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
}
