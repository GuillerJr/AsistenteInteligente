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
        var normalized = transcript
            .folding(
                options: [.caseInsensitive, .diacriticInsensitive],
                locale: Locale(identifier: "es")
            )
            .lowercased()
            .replacingOccurrences(of: ",", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        if normalized.hasPrefix("jarvis ") {
            normalized.removeFirst("jarvis ".count)
        }
        return commands.contains(normalized) ? .activeApplication : nil
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
