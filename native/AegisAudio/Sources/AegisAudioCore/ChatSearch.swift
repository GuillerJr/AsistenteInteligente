import CoreFoundation
import Foundation

public struct ChatSearchMatch: Equatable, Sendable {
    public let turnID: UUID
    public let sequence: Int
    public let excerpt: String

    public init(turnID: UUID, sequence: Int, excerpt: String) {
        self.turnID = turnID; self.sequence = sequence; self.excerpt = excerpt
    }

    init(_ value: Any) throws {
        guard let row = value as? [String: Any],
              let rawID = row["turn_id"] as? String, let id = UUID(uuidString: rawID),
              let sequence = row["sequence"] as? Int, (1...10_000).contains(sequence),
              let number = row["sequence"] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue == Double(sequence),
              let excerpt = row["excerpt"] as? String, !excerpt.isEmpty,
              excerpt.count <= 194, excerpt.utf8.count <= 776 else {
            throw LocalIPCError.malformedResponse
        }
        self.init(turnID: id, sequence: sequence, excerpt: excerpt)
    }
}

public struct ChatSearchPage: Sendable {
    public let conversations: [ChatConversationSummary]
    public let nextCursor: String?

    public init(_ data: Data, archived: Bool, filed: Bool) throws {
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              payload.keys.contains("next_cursor"),
              let rows = payload["conversations"] as? [[String: Any]], rows.count <= 50,
              rows.allSatisfy({ row in
                  row.keys.contains("search_match") && ["archived", "filed"].allSatisfy { field in
                      guard let value = row[field] as? NSNumber else { return false }
                      return CFGetTypeID(value) == CFBooleanGetTypeID()
                  }
              }) else { throw LocalIPCError.malformedResponse }
        let records = try ChatConversationSummary.page(data).0
        guard Set(records.map(\.id)).count == records.count,
              records.allSatisfy({ $0.archived == archived && (archived || $0.filed == filed) })
        else { throw LocalIPCError.malformedResponse }
        if payload["next_cursor"] is NSNull { nextCursor = nil }
        else {
            guard let cursor = payload["next_cursor"] as? String,
                  Self.validCursor(cursor) else { throw LocalIPCError.malformedResponse }
            nextCursor = cursor
        }
        conversations = records
    }

    private static func validCursor(_ value: String) -> Bool {
        guard value.utf8.count <= 2_048 else { return false }
        let parts = value.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 2, (1...1_980).contains(parts[0].utf8.count),
              parts[1].utf8.count == 64 else { return false }
        return parts[0].utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0)
                || $0 == 45 || $0 == 95
        } && parts[1].utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
