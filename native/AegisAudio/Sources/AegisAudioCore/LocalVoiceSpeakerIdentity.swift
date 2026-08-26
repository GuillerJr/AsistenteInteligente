import Foundation

public enum LocalVoiceSpeakerIdentityCommand: Equatable, Sendable {
    case identifySpeaker

    private static let commands: Set<String> = [
        "quien soy",
        "me reconoces",
        "sabes quien soy",
        "puedes reconocerme",
        "who am i",
        "do you recognize me",
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
            normalized = normalized
                .trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
                .split(whereSeparator: { $0.isWhitespace })
                .joined(separator: " ")
        }
        return commands.contains(normalized) ? .identifySpeaker : nil
    }

    public static func spokenIdentifier(_ value: String?) -> String? {
        guard let value, SpeakerIdentityCapability.isValidSpeakerLabel(value) else {
            return nil
        }
        return value
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }
}
