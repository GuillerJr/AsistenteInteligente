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
        commands.contains(LocalVoiceCommandText.normalize(transcript))
            ? .identifySpeaker
            : nil
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
