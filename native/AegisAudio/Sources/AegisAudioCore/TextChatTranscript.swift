import Foundation

/// A bounded transcript. Only the active job can change its assistant message.
public struct TextChatTranscript: Sendable {
    public struct Message: Identifiable, Equatable, Sendable {
        public enum Role: Sendable { case user, assistant }
        public let id: UUID
        public let role: Role
        public var text: String
        public var interrupted: Bool
    }

    public private(set) var messages: [Message] = []
    public private(set) var jobID: UUID?
    private var assistantID: UUID?
    private var partial = ""
    public init() {}

    public mutating func begin(_ text: String) {
        messages.append(Message(id: UUID(), role: .user, text: text, interrupted: false))
        let message = Message(id: UUID(), role: .assistant, text: "", interrupted: false)
        messages.append(message)
        assistantID = message.id
        partial = ""
        if messages.count > 100 { messages.removeFirst(messages.count - 100) }
    }

    public mutating func attach(_ jobID: UUID) { self.jobID = jobID }

    public mutating func update(_ text: String, jobID: UUID) -> Bool {
        guard self.jobID == jobID, text.utf8.count <= IPCJobStatusEvent.maximumResultBytes,
              text.hasPrefix(partial), let index = assistantIndex else { return false }
        partial = text
        messages[index].text = text
        return true
    }

    public mutating func finish(_ text: String, jobID: UUID? = nil, interrupted: Bool = false) {
        guard jobID == nil || self.jobID == jobID, let index = assistantIndex else { return }
        messages[index].text = interrupted && !messages[index].text.isEmpty
            ? messages[index].text + "\n\n" + text : text
        messages[index].interrupted = interrupted
        self.jobID = nil
        assistantID = nil
        partial = ""
    }

    private var assistantIndex: Int? {
        guard let assistantID else { return nil }
        return messages.firstIndex { $0.id == assistantID }
    }
}
