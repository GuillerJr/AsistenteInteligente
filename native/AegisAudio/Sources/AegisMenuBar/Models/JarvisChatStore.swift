import AegisAudioCore
import Foundation
import Observation

@MainActor @Observable
final class JarvisChatStore {
    var transcript = TextChatTranscript()
    var conversationID: UUID?
    // Distinguish initial startup from the owner's explicit "new chat".
    // A verified voice can resume its recent bound session after restarting.
    var hasExplicitConversationSelection = false
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
    var notice: String?
    var historyGeneration = UUID()
    var transcriptRevision = UUID()
    /// A temporary chat (like ChatGPT's): not listed, never uses or creates memories,
    /// and deleted as soon as the owner leaves it (the daemon purges any left behind).
    var temporary = false
    /// Temporary chats left by the owner, waiting for their deletion.
    var abandonedTemporaryChats: [UUID] = []
    private var temporaryConversationID: UUID?
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

    func newTemporaryConversation() {
        guard !isEditing else { return }
        resetConversation()
        temporary = true
    }

    func adoptTemporaryConversation(_ id: UUID) {
        guard temporary else { return }
        temporaryConversationID = id
    }

    func resetConversation() {
        if let leaving = temporaryConversationID {
            abandonedTemporaryChats.append(leaving)
        }
        temporaryConversationID = nil
        temporary = false
        hasExplicitConversationSelection = true
        transcriptRevision = UUID()
        transcript = TextChatTranscript()
        conversationID = nil
        selectedConversation = nil
        inputJobID = nil
        nextBeforeSequence = nil
        attachments = []
        error = nil
        notice = nil
    }

    /// Delete the temporary chats the owner left; a failure is retried on the next leave
    /// and the daemon purges any that remain at startup.
    func deleteAbandonedTemporaryChats(model: MenuBarModel) async {
        let pending = abandonedTemporaryChats
        for id in pending where await model.performChatIPC("conversations.delete", payload: [
            "conversation_id": id.uuidString.lowercased()
        ]) != nil {
            abandonedTemporaryChats.removeAll { $0 == id }
        }
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
