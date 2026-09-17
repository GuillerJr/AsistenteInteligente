import AegisAudioCore
import Foundation
import Observation

@MainActor @Observable
final class JarvisChatStore {
    var transcript = TextChatTranscript()
    var conversationID: UUID?
    var selectedConversation: ChatConversationSummary?
    var inputJobID: UUID?
    var submitting = false
    var cancelling = false
    var showHistory = true
    var historyQuery = ""
    var showTrash = false
    var conversations: [ChatConversationSummary] = []
    var nextOffset: Int?
    var nextBeforeSequence: Int?
    var historyLoading = false
    var selectionLoading = false
    var importing = false
    var attachments: [ChatAttachment] = []
    var error: String?
    var historyGeneration = UUID()
    var isBusy: Bool { submitting || transcript.jobID != nil }
    var isEditing: Bool { isBusy || selectionLoading || importing }
    var currentConversation: ChatConversationSummary? {
        if let selectedConversation, selectedConversation.id == conversationID { return selectedConversation }
        return conversations.first(where: { $0.id == conversationID && !$0.archived })
    }
    var currentTitle: String {
        currentConversation?.title
            ?? transcript.messages.first(where: { $0.role == .user }).map { String($0.text.prefix(128)) }
            ?? "Chat con Jarvis"
    }

    func newConversation() {
        guard !isEditing else { return }
        resetConversation()
    }

    func resetConversation() {
        transcript = TextChatTranscript()
        conversationID = nil
        selectedConversation = nil
        inputJobID = nil
        nextBeforeSequence = nil
        attachments = []
        error = nil
    }
}
