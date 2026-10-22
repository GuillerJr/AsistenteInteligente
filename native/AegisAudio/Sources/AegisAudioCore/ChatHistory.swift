import Foundation

public struct ChatConversationSummary: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let title: String
    public let updatedAt: String
    public let archived: Bool
    /// The project the chat belongs to, if any.
    public var projectID: UUID? = nil
    public var filed: Bool = false

    public init(id: UUID, title: String, updatedAt: String, archived: Bool,
                projectID: UUID? = nil, filed: Bool = false) {
        self.id = id; self.title = title; self.updatedAt = updatedAt; self.archived = archived
        self.projectID = projectID; self.filed = filed
    }

    public static func record(_ data: Data) throws -> Self {
        guard let row = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw LocalIPCError.malformedResponse
        }
        let wrapper = try JSONSerialization.data(withJSONObject: ["conversations": [row]])
        guard let record = try page(wrapper).0.first else { throw LocalIPCError.malformedResponse }
        return record
    }

    public static func page(_ data: Data) throws -> ([Self], Int?) {
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = payload["conversations"] as? [[String: Any]], rows.count <= 50 else {
            throw LocalIPCError.malformedResponse
        }
        let summaries = try rows.map { row -> Self in
            guard let raw = row["conversation_id"] as? String, let id = UUID(uuidString: raw),
                  let date = row["updated_at"] as? String else { throw LocalIPCError.malformedResponse }
            return Self(id: id, title: row["title"] as? String ?? "Conversación sin título",
                        updatedAt: date, archived: row["archived"] as? Bool ?? false,
                        projectID: (row["project_id"] as? String).flatMap(UUID.init(uuidString:)),
                        filed: row["filed"] as? Bool ?? false)
        }
        return (summaries, payload["next_offset"] as? Int)
    }
}

public struct ChatHistoryPage: Sendable {
    public let conversationID: UUID
    public let messages: [TextChatTranscript.Message]
    public let nextBeforeSequence: Int?

    public init(_ data: Data) throws {
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = payload["conversation_id"] as? String, let conversationID = UUID(uuidString: raw),
              let turns = payload["turns"] as? [[String: Any]], turns.count <= 20 else {
            throw LocalIPCError.malformedResponse
        }
        var lastSequence = 0
        var messages: [TextChatTranscript.Message] = []
        for turn in turns {
            guard turn["conversation_id"] as? String == raw,
                  let rawID = turn["turn_id"] as? String, let id = UUID(uuidString: rawID),
                  let text = turn["content"] as? String, text.utf8.count <= 16_384,
                  let sequence = turn["sequence"] as? Int, sequence > lastSequence,
                  let role = turn["role"] as? String, ["user", "assistant"].contains(role)
            else { throw LocalIPCError.malformedResponse }
            lastSequence = sequence
            messages.append(.init(id: id, role: role == "user" ? .user : .assistant, text: text,
                                  attachmentNames: turn["attachment_names"] as? [String] ?? [],
                                  truncated: turn["truncated"] as? Bool ?? false, sequence: sequence,
                                  feedback: turn["owner_feedback"] as? String))
        }
        self.conversationID = conversationID
        self.messages = messages
        nextBeforeSequence = payload["next_before_sequence"] as? Int
    }
}
