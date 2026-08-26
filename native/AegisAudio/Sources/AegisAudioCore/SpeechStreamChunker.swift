import Foundation

public struct SpeechStreamChunker: Sendable {
    private var latestSnapshot = ""
    private var emittedCharacters = 0

    public init() {}

    public mutating func consume(_ snapshot: String) -> [String] {
        guard snapshot.hasPrefix(latestSnapshot) else {
            reset()
            return []
        }
        latestSnapshot = snapshot
        var pending = String(snapshot.dropFirst(emittedCharacters))
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
        guard finalText.hasPrefix(latestSnapshot) else { return chunks }
        let remainder = String(finalText.dropFirst(emittedCharacters))
        let normalized = Self.normalized(remainder)
        if !normalized.isEmpty {
            chunks.append(normalized)
            emittedCharacters = finalText.count
        }
        latestSnapshot = finalText
        return chunks
    }

    public mutating func reset() {
        latestSnapshot = ""
        emittedCharacters = 0
    }

    private static func completeBoundary(in text: String) -> String.Index? {
        var index = text.startIndex
        while index < text.endIndex {
            let character = text[index]
            let next = text.index(after: index)
            if (character == "." || character == "!" || character == "?" || character == "\n"),
               next < text.endIndex,
               text[next].isWhitespace
            {
                return index
            }
            index = next
        }
        return nil
    }

    private static func normalized(_ text: String) -> String {
        text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }
}
