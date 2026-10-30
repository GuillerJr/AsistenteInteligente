import CryptoKit
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

/// Counts and the reviewed suffix's identity only. The original transcription,
/// pre-trigger words and audio are never retained by this receipt.
public struct LocalWakePhraseProjectionMetadata: Equatable, Sendable {
    public let matchCount: Int
    public let triggerCharacterOffset: Int
    public let discardedPrefixCharacters: Int
    public let suffixUTF8Bytes: Int
    public let suffixSHA256: String
}

public struct LocalWakePhraseProjection: Equatable, Sendable {
    public let text: String
    public let metadata: LocalWakePhraseProjectionMetadata
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
        reviewedProjection(in: transcription, phrase: phrase)?.text
    }

    /// Always use the first match. A later mention can be legitimate dictated
    /// content; counting it is observability, never another activation or gate.
    public static func reviewedProjection(in transcription: String, phrase: String) -> LocalWakePhraseProjection? {
        guard transcription.utf8.count <= 16000, isValid(phrase) else { return nil }
        let sought = words(phrase).map(\.0), heard = words(transcription)
        guard heard.count >= sought.count, heard.count <= 4096 else { return nil }
        let matches = (0...(heard.count - sought.count)).filter { index in
            Array(heard[index..<(index + sought.count)].map(\.0)) == sought
        }
        guard let index = matches.first else { return nil }
        let source = transcription as NSString
        let end = NSMaxRange(heard[index + sought.count - 1].1)
        let suffix = source.substring(from: end)
        let separator = CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",.;:!?¡¿—–-"))
        let delimiters = String(suffix.prefix(while: { $0.unicodeScalars.allSatisfy(separator.contains) }))
        // Only the delimiter immediately after the trigger is discarded.
        // Punctuation belonging to the dictated payload must survive.
        let text = String(suffix.drop(while: { $0.unicodeScalars.allSatisfy(separator.contains) }))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let hash = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
        return LocalWakePhraseProjection(text: text, metadata: .init(
            matchCount: matches.count,
            triggerCharacterOffset: source.substring(to: heard[index].1.location).count,
            discardedPrefixCharacters: (source.substring(to: end) + delimiters).count,
            suffixUTF8Bytes: text.utf8.count, suffixSHA256: hash))
    }
}
