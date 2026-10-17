import Foundation

public struct SpeechStreamChunker: Sendable {
    private static let minimumUtteranceCharacters = 120
    private static let minimumClauseCharacters = 48
    private static let maximumBufferedCharacters = 320
    private static let maximumSpokenCharacters = 2_000
    private var latestSnapshot = ""
    private var emittedCharacters = 0
    public private(set) var isInvalid = false

    public init() {}

    public mutating func consume(_ snapshot: String) -> [String] {
        guard !isInvalid else { return [] }
        guard snapshot.hasPrefix(latestSnapshot) else {
            isInvalid = true
            return []
        }
        latestSnapshot = snapshot
        let boundedSnapshot = String(snapshot.prefix(Self.maximumSpokenCharacters))
        var pending = String(boundedSnapshot.dropFirst(emittedCharacters))
        var chunks: [String] = []
        while let boundary = Self.completeBoundary(in: pending) {
            let end = pending.index(after: boundary)
            var consumedEnd = end
            while consumedEnd < pending.endIndex, pending[consumedEnd].isWhitespace {
                consumedEnd = pending.index(after: consumedEnd)
            }
            let raw = String(pending[..<consumedEnd])
            let normalized = Self.normalized(raw)
            if !normalized.isEmpty {
                chunks.append(normalized)
            }
            emittedCharacters += raw.count
            pending = String(pending[consumedEnd...])
        }
        return chunks
    }

    public mutating func finish(_ finalText: String) -> [String] {
        var chunks = consume(finalText)
        guard !isInvalid else { return [] }
        let boundedText = String(finalText.prefix(Self.maximumSpokenCharacters))
        let remainder = String(boundedText.dropFirst(emittedCharacters))
        let normalized = Self.normalized(remainder)
        if !normalized.isEmpty {
            chunks.append(normalized)
            emittedCharacters = boundedText.count
        }
        latestSnapshot = finalText
        return chunks
    }

    public mutating func reset() {
        latestSnapshot = ""
        emittedCharacters = 0
        isInvalid = false
    }

    private static func completeBoundary(in text: String) -> String.Index? {
        var index = text.startIndex
        var characters = 0
        var clauseBoundary: String.Index?
        var wordBoundary: String.Index?
        while index < text.endIndex {
            let character = text[index]
            let next = text.index(after: index)
            characters += 1
            var punctuationEnd = next
            while punctuationEnd < text.endIndex,
                  "\"'»”’)]}".contains(text[punctuationEnd]) {
                punctuationEnd = text.index(after: punctuationEnd)
            }
            let hasFollowingSpace = punctuationEnd < text.endIndex
                && text[punctuationEnd].isWhitespace
            if characters >= minimumUtteranceCharacters,
               (character == "." || character == "!" || character == "?"),
               hasFollowingSpace,
               character != "." || !isAbbreviation(in: text, at: index) {
                return text.index(before: punctuationEnd)
            }
            if characters >= minimumClauseCharacters,
               (character == "," || character == ";" || character == ":"),
               next < text.endIndex,
               text[next].isWhitespace {
                clauseBoundary = index
            }
            if character.isWhitespace { wordBoundary = index }
            // Only cut an unfinished thought when its bounded buffer is full.
            // A comma after 48 characters alone must not restart Ray's prosody.
            if characters >= maximumBufferedCharacters {
                return clauseBoundary ?? wordBoundary
            }
            index = next
        }
        return nil
    }

    private static func isAbbreviation(in text: String, at period: String.Index) -> Bool {
        let prefix = text[..<period]
        let token = prefix.split(whereSeparator: { $0.isWhitespace }).last.map(String.init) ?? ""
        let folded = token.lowercased().trimmingCharacters(in: .punctuationCharacters)
        let abbreviations: Set<String> = [
            "sr", "sra", "srta", "dr", "dra", "ud", "uds", "ing", "lic", "prof",
            "av", "aprox", "pág", "págs", "núm", "ej", "p.ej", "a.m", "p.m",
        ]
        return abbreviations.contains(folded)
            || (token.count == 1 && token.first?.isUppercase == true)
            || (token.allSatisfy(\.isNumber) && prefix.trimmingCharacters(in: .whitespaces).count == token.count)
    }

    private static func normalized(_ text: String) -> String {
        text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }
}
