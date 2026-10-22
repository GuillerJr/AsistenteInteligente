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
    var showArchived = false
    var searchFocusPending = false
    var browserPending = false
    var deletePending = false
    var searchFocusRequest = UUID()
    var browserRequest = UUID()
    var deleteRequest = UUID()
    var composerDraft: String?
    @ObservationIgnored var draftCache = ChatDraftCache()
    var feedbackUpdating: Set<UUID> = []
    var completedTurnPersisted = false
    var activeSubmission: ChatSubmission?
    var retrySubmission: ChatSubmission?
    var retryConversationID: UUID?
    var retryAvailable: Bool { retrySubmission != nil && retryConversationID == conversationID && !isEditing }
    var conversations: [ChatConversationSummary] = []
    var nextOffset: Int?
    var nextBeforeSequence: Int?
    var historyLoading = false
    var selectionLoading = false
    var importing = false
    var attachments: [ChatAttachment] = []
    var error: String?
    var notice: String?
    var appGrantWarning: String?
    var historyGeneration = UUID()
    var transcriptRevision = UUID()
    /// A temporary chat (like ChatGPT's): not listed, never uses or creates memories,
    /// and deleted as soon as the owner leaves it (the daemon purges any left behind).
    var temporary = false
    /// What the main area shows: the conversation, Dot's page or the scheduled tasks.
    enum Pane: Equatable { case chat, dot, scheduled, project(String) }
    var pane = Pane.chat
    /// The owner's projects, and the one a new chat will start in.
    var projects: [JarvisProject] = []
    var newChatProjectID: String?
    /// Codex's plan mode: Jarvis only reads and answers with a plan to implement.
    var planMode = false
    /// Replies that are plans, and the ones the owner already sent to be implemented.
    var planReplies: Set<UUID> = []
    var implementedPlans: Set<UUID> = []
    /// Temporary chats left by the owner, waiting for their deletion.
    var abandonedTemporaryChats: [UUID] = []
    private var temporaryConversationID: UUID?
    /// Scheduled tasks and goals waiting for the owner ("Requiere tu atención").
    var attention: [AttentionEntry] = []

    struct AttentionEntry: Identifiable, Equatable, Sendable {
        let id: String
        let title: String
        let reason: String
        let action: String
        let conversationID: UUID?
    }
    var isBusy: Bool { submitting || transcript.jobID != nil }
    var isEditing: Bool { isBusy || selectionLoading || importing }
    var currentConversation: ChatConversationSummary? {
        if let selectedConversation, selectedConversation.id == conversationID { return selectedConversation }
        return conversations.first(where: { $0.id == conversationID && !$0.archived })
    }
    var currentTitle: String {
        currentConversation?.title
            ?? transcript.messages.first(where: { $0.role == .user }).map { String($0.text.prefix(128)) }
            ?? String(localized: "Chat con Jarvis", bundle: .module)
    }

    func newConversation(inProject projectID: String? = nil) {
        guard !isEditing else { return }
        resetConversation()
        newChatProjectID = projectID
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

    func resetConversation(preservingDraft: Bool = true) {
        let discarded = preservingDraft ? retainCurrentDraft() : .none
        pane = .chat
        newChatProjectID = nil
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
        composerDraft = nil
        feedbackUpdating = []
        activeSubmission = nil
        completedTurnPersisted = false
        retrySubmission = nil
        retryConversationID = nil
        planReplies = []
        implementedPlans = []
        error = nil
        switch discarded {
        case .none: notice = nil
        case .oldest:
            notice = String(localized: "Se descartaron los borradores menos recientes para limitar el uso de memoria. Los borradores solo se conservan mientras Jarvis está abierto.", bundle: .module)
        case .oversized:
            notice = String(localized: "El borrador anterior superaba el límite de memoria y no se conservó al cambiar de chat.", bundle: .module)
        }
        appGrantWarning = nil
    }

    func refreshAttention(model: MenuBarModel, snoozing: String? = nil) async {
        guard let data = await model.performChatIPC(
            snoozing == nil ? "attention.list" : "attention.snooze",
            payload: snoozing.map { ["item_id": $0] } ?? [:]
        ), let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let items = payload["items"] as? [[String: Any]] else { return }
        attention = Self.attentionEntries(items)
    }

    nonisolated static func attentionEntries(_ items: [[String: Any]]) -> [AttentionEntry] {
        items.prefix(20).compactMap { item in
            guard let id = item["item_id"] as? String, let title = item["title"] as? String,
                  let reason = item["reason"] as? String, let action = item["action"] as? String
            else { return nil }
            return AttentionEntry(
                id: id, title: String(title.prefix(80)), reason: String(reason.prefix(200)),
                action: action,
                conversationID: (item["conversation_id"] as? String).flatMap(UUID.init(uuidString:))
            )
        }
    }

    func resolveAttention(_ entry: AttentionEntry, model: MenuBarModel) async {
        let parts = entry.id.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return }
        let method = entry.action == "run_task" ? "schedules.run_now" : "goals.resume"
        let key = entry.action == "run_task" ? "task_id" : "goal_id"
        _ = await model.performChatIPC(method, payload: [key: parts[1]])
        notice = entry.action == "run_task" ? String(localized: "Reintentando «\(entry.title)».", bundle: .module)
                                            : String(localized: "Jarvis retomó «\(entry.title)».", bundle: .module)
        await refreshAttention(model: model)
    }

    func openAttention(_ entry: AttentionEntry, model: MenuBarModel) async {
        guard let id = entry.conversationID else { return }
        if let summary = conversations.first(where: { $0.id == id }) {
            await open(summary, model: model)
            return
        }
        let row: [String: Any] = ["conversation_id": id.uuidString.lowercased(), "title": entry.title,
                                  "updated_at": ISO8601DateFormatter().string(from: Date()),
                                  "archived": false]
        if let data = try? JSONSerialization.data(withJSONObject: row),
           let summary = try? ChatConversationSummary.record(data) {
            await open(summary, model: model)
        }
    }

    /// Delete the temporary chats the owner left; a failure is retried on the next leave
    /// and the daemon purges any that remain at startup.
    func deleteAbandonedTemporaryChats(model: MenuBarModel) async {
        let pending = abandonedTemporaryChats
        for id in pending where await model.performChatIPC("conversations.delete", payload: [
            "conversation_id": id.uuidString.lowercased()
        ]) != nil {
            discardDraft(for: id)
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
        } catch { self.error = String(localized: "No pude actualizar la conversación de voz.", bundle: .module) }
    }
}
