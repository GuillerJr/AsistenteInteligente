import Foundation

public enum LocalVoiceApplicationContextCommand: Equatable, Sendable {
    case activeApplication

    private static let commands: Set<String> = [
        "cual es la aplicacion activa",
        "que aplicacion esta activa",
        "que aplicacion estoy usando",
        "que app estoy usando",
        "what app am i using",
        "what is the active app",
    ]

    public static func parse(_ transcript: String) -> Self? {
        commands.contains(LocalVoiceCommandText.normalize(transcript))
            ? .activeApplication
            : nil
    }

    public static func sanitizedApplicationName(_ value: String?) -> String? {
        guard
            let value,
            !value.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
        else {
            return nil
        }
        let normalized = value
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard !normalized.isEmpty, normalized.utf8.count <= 160 else { return nil }
        return normalized
    }
}
