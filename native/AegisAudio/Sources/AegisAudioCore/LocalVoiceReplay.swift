import Foundation

public enum LocalVoiceReplayCommand: Equatable, Sendable {
    case replay

    private static let commands: Set<String> = [
        "dilo otra vez",
        "puedes repetirlo",
        "repite",
        "repite la ultima respuesta",
        "repite tu respuesta",
        "repitelo",
        "repeat that",
        "repeat your last answer",
        "say that again",
    ]

    public static func parse(_ transcript: String) -> Self? {
        commands.contains(LocalVoiceCommandText.normalize(transcript)) ? .replay : nil
    }

    public static let unavailableSpokenResponse =
        "No tengo una respuesta reciente para repetir."
    public static let unverifiedSpokenResponse =
        "No la repetí porque no pude verificar al propietario."
}

public struct LocalVoiceReplayBuffer: Sendable {
    public static let retention: TimeInterval = 5 * 60
    private static let maximumCharacters = 2_000

    private var text: String?
    private var storedAt: TimeInterval?

    public init() {}

    @discardableResult
    public mutating func store(_ response: String, at uptime: TimeInterval) -> Bool {
        let normalized = response.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard uptime.isFinite, uptime >= 0, !normalized.isEmpty else {
            clear()
            return false
        }
        text = String(normalized.prefix(Self.maximumCharacters))
        storedAt = uptime
        return true
    }

    public mutating func response(at uptime: TimeInterval) -> String? {
        guard
            uptime.isFinite,
            let text,
            let storedAt,
            uptime >= storedAt,
            uptime - storedAt < Self.retention
        else {
            clear()
            return nil
        }
        return text
    }

    public mutating func clear() {
        text = nil
        storedAt = nil
    }
}
