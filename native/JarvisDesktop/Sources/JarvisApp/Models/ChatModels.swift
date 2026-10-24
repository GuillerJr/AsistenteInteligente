import DesktopCore
import Foundation

struct ChatSession: Identifiable, Hashable {
    let id: String
    let title: String
    let temporary: Bool
    let archived: Bool
    var scope: SessionScope { SessionScope(sessionID: id, temporary: temporary) }

    init?(_ value: RPCValue) {
        guard let id = value["session_id"].string, let title = value["title"].string,
              let temporary = value["temporary"].bool else { return nil }
        self.id = id; self.title = title; self.temporary = temporary
        archived = value["archived"].bool ?? false
    }
}

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: String
    let text: String
}

struct RunActivity: Identifiable {
    let id: Int
    let kind: String
}

struct PendingSubmission {
    let id: String
    let session: ChatSession
    let text: String
}
