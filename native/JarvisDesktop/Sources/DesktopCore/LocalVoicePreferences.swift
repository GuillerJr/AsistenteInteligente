import Foundation

public enum LocalVoiceCaptureMode: String, CaseIterable, Hashable, Sendable {
    case dictation, extendedDictation, wakePhrase

    public var label: String {
        switch self {
        case .dictation: "Dictado · 30 segundos"
        case .extendedDictation: "Dictado prolongado · 2 minutos"
        case .wakePhrase: "Frase de activación · 5 minutos"
        }
    }

    public var durationMultiplier: Double {
        switch self { case .dictation: 1; case .extendedDictation: 4; case .wakePhrase: 10 }
    }
}

/// Session-local preferences. Selecting a setting never starts capture, requests
/// permission, downloads a model, or connects a provider.
public struct LocalVoicePreferences: Equatable, Sendable {
    public var localeIdentifier = "es-ES"
    public var voiceID: String?
    public var mode: LocalVoiceCaptureMode = .dictation
    public var wakePhrase = "Jarvis"
    public init() {}
}

public enum LocalWakePhrasePolicy {
    private static let expression = try! NSRegularExpression(pattern: "[\\p{L}\\p{N}]+")

    private static func words(_ value: String) -> [(String, NSRange)] {
        let range = NSRange(value.startIndex..., in: value)
        return expression.matches(in: value, range: range).map { match in
            let token = (value as NSString).substring(with: match.range)
            return (token.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "en_US_POSIX")), match.range)
        }
    }

    public static func isValid(_ phrase: String) -> Bool {
        guard phrase.utf8.count <= 64, !phrase.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else { return false }
        let parts = words(phrase)
        return (1...4).contains(parts.count) && parts.allSatisfy { !$0.0.isEmpty }
    }

    /// Exact normalized words, never a substring such as "Jarvisito". The
    /// result preserves the original suffix but omits all pre-trigger audio.
    public static func reviewedSuffix(in transcription: String, phrase: String) -> String? {
        guard transcription.utf8.count <= 16000, isValid(phrase) else { return nil }
        let sought = words(phrase).map(\.0), heard = words(transcription)
        guard heard.count >= sought.count, heard.count <= 4096 else { return nil }
        for index in 0...(heard.count - sought.count) {
            guard Array(heard[index..<(index + sought.count)].map(\.0)) == sought else { continue }
            let end = NSMaxRange(heard[index + sought.count - 1].1)
            let suffix = (transcription as NSString).substring(from: end)
            let separator = CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",.;:!?¡¿—–-"))
            // Only the delimiter immediately after the trigger is discarded.
            // Punctuation belonging to the dictated payload must survive.
            return String(suffix.drop(while: { $0.unicodeScalars.allSatisfy(separator.contains) }))
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return nil
    }
}
