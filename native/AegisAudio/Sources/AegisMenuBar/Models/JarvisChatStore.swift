import AegisAudioCore
import Foundation
import Observation

@MainActor @Observable
final class JarvisChatStore {
    var transcript = TextChatTranscript()
    var conversationID: UUID?
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

    func newConversation() {
        guard !isEditing else { return }
        resetConversation()
    }

    func resetConversation() {
        transcript = TextChatTranscript()
        conversationID = nil
        inputJobID = nil
        nextBeforeSequence = nil
        attachments = []
        error = nil
    }
}
