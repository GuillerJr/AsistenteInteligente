import DesktopCore
import Foundation

struct ChatSession: Identifiable, Hashable {
    let id: String
    let title: String
    let temporary: Bool
    let archived: Bool
    let agentID: String
    let parentSessionID: String?
    var scope: SessionScope { SessionScope(sessionID: id, temporary: temporary) }

    init?(_ value: RPCValue) {
        guard let id = value["session_id"].string, let title = value["title"].string,
              let temporary = value["temporary"].bool,
              ManagementValidation.identifier(id),
              let agentID = value["agent_id"].string, ManagementValidation.identifier(agentID, agent: true)
        else { return nil }
        if value["parent_session_id"] != .null {
            guard let parent = value["parent_session_id"].string, ManagementValidation.identifier(parent) else { return nil }
        }
        self.id = id; self.title = title; self.temporary = temporary
        archived = value["archived"].bool ?? false
        self.agentID = agentID; parentSessionID = value["parent_session_id"].string
    }
}

struct ChatMessage: Identifiable, Equatable {
    let id: String
    let role: String
    let text: String
    let sequence: Int?
    init(id: String, role: String, text: String, sequence: Int? = nil) {
        self.id = id; self.role = role; self.text = text; self.sequence = sequence
    }
}

struct RunActivity: Identifiable {
    let id: Int
    let kind: String
}

struct PendingSubmission {
    let id: String
    let session: ChatSession
    let text: String
    var attachmentIDs: [String] = []
}
