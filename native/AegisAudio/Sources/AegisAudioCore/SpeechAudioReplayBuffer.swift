import CryptoKit
import Foundation

/// Keeps only the last successfully played voice, never a second synthesizer.
/// Owner/presence authorization remains the caller's responsibility.
public struct SpeechAudioReplayBuffer: Sendable {
    public static let maximumBytes = 4_194_304
    public static let maximumChunks = 4_096
    private var digest: Data?
    private var chunks: [Data] = []
    private var storedAt: TimeInterval?

    public init() {}

    @discardableResult
    public mutating func store(
        text: String, chunks: [Data], at uptime: TimeInterval
    ) -> Bool {
        clear()
        guard uptime.isFinite, uptime >= 0, !chunks.isEmpty,
              chunks.count <= Self.maximumChunks,
              let digest = Self.textDigest(text)
        else { return false }
        var total = 0
        for chunk in chunks {
            guard !chunk.isEmpty, chunk.count.isMultiple(of: 2),
                  chunk.count <= IPCSpeechStreamEvent.maximumPCMBytes
            else { return false }
            total += chunk.count
            guard total <= Self.maximumBytes else { return false }
        }
        self.digest = digest
        self.chunks = chunks
        storedAt = uptime
        return true
    }

    public mutating func audio(matching text: String, at uptime: TimeInterval) -> [Data]? {
        guard uptime.isFinite, let storedAt, uptime >= storedAt,
              uptime - storedAt < LocalVoiceReplayBuffer.retention
        else {
            clear()
            return nil
        }
        guard let requested = Self.textDigest(text), requested == digest else { return nil }
        return chunks
    }

    public mutating func clear() {
        digest = nil
        chunks.removeAll(keepingCapacity: false)
        storedAt = nil
    }

    private static func textDigest(_ text: String) -> Data? {
        let normalized = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard !normalized.isEmpty, normalized.unicodeScalars.count <= 2_000,
              normalized.utf8.count <= 8_192
        else { return nil }
        return Data(SHA256.hash(data: Data(normalized.utf8)))
    }
}
