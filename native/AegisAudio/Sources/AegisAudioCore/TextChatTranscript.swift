import Foundation

/// A bounded transcript. Only the active job can change its assistant message.
public struct TextChatTranscript: Sendable {
    public static let maximumVisibleMessages = 1_000
    public struct Message: Identifiable, Equatable, Sendable {
        public enum Role: Sendable { case user, assistant }
        public let id: UUID
        public let role: Role
        public var text: String
        public var interrupted: Bool
        public var attachmentNames: [String]
        public var truncated: Bool
        public var sequence: Int?
        public var feedback: String?

        public init(id: UUID = UUID(), role: Role, text: String, interrupted: Bool = false,
                    attachmentNames: [String] = [], truncated: Bool = false,
                    sequence: Int? = nil, feedback: String? = nil) {
            self.id = id; self.role = role; self.text = text; self.interrupted = interrupted
            self.attachmentNames = attachmentNames; self.truncated = truncated
            self.sequence = sequence; self.feedback = feedback
        }
    }

    public private(set) var messages: [Message] = []
    public private(set) var jobID: UUID?
    public private(set) var hasNewerHistory = false
    private var assistantID: UUID?
    private var partial = ""
    public init() {}

    @discardableResult
    public mutating func begin(_ text: String, attachmentNames: [String] = []) -> Bool {
        // A historical window must return to the recent page before a live turn
        // can be appended; otherwise it would conceal the gap between them.
        guard !hasNewerHistory else { return false }
        messages.append(Message(id: UUID(), role: .user, text: text, attachmentNames: attachmentNames))
        let message = Message(id: UUID(), role: .assistant, text: "", interrupted: false)
        messages.append(message)
        assistantID = message.id
        partial = ""
        if messages.count > Self.maximumVisibleMessages {
            messages.removeFirst(messages.count - Self.maximumVisibleMessages)
        }
        return true
    }

    public mutating func attach(_ jobID: UUID) { self.jobID = jobID }

    public mutating func restore(_ restored: [Message], hasNewerHistory: Bool = false) {
        guard jobID == nil else { return }
        messages = Array(restored.suffix(Self.maximumVisibleMessages))
        self.hasNewerHistory = hasNewerHistory
        assistantID = nil
        partial = ""
    }

    public mutating func prepend(_ older: [Message]) {
        guard jobID == nil else { return }
        let existing = Set(messages.map(\.id))
        let combined = older.filter { !existing.contains($0.id) } + messages
        hasNewerHistory = hasNewerHistory || combined.count > Self.maximumVisibleMessages
        messages = Array(combined.prefix(Self.maximumVisibleMessages))
    }

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

    public mutating func setFeedback(_ value: String?, messageID: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == messageID && $0.role == .assistant }) else { return }
        messages[index].feedback = value
    }

    private var assistantIndex: Int? {
        guard let assistantID else { return nil }
        return messages.firstIndex { $0.id == assistantID }
    }
}
