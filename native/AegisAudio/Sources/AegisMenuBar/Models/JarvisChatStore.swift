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
    var transcriptRevision = UUID()
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
        transcriptRevision = UUID()
        transcript = TextChatTranscript()
        conversationID = nil
        selectedConversation = nil
        inputJobID = nil
        nextBeforeSequence = nil
        attachments = []
        error = nil
    }

    /// Mirror a completed voice turn without marking it as a text job or
    /// interrupting its spoken playback. A newer selection/turn wins the race.
    func refreshVoiceTranscript(conversationID id: UUID, model: MenuBarModel) async {
        guard model.canReadChatHistory, conversationID == id, !isEditing else { return }
        let revision = transcriptRevision
        guard let data = await model.performChatIPC("conversations.messages", payload: [
            "conversation_id": id.uuidString.lowercased()
        ]), conversationID == id, transcriptRevision == revision, !isEditing else { return }
        do {
            let page = try ChatHistoryPage(data)
            guard page.conversationID == id else { throw LocalIPCError.malformedResponse }
            transcript.restore(page.messages)
            nextBeforeSequence = page.nextBeforeSequence
            await refreshHistory(model: model)
        } catch { self.error = "No pude actualizar la conversación de voz." }
    }
}
