import AegisAudioCore
import Foundation

extension JarvisChatStore {
    func refreshHistory(model: MenuBarModel, append: Bool = false) async {
        guard model.canReadChatHistory, !append || !historyLoading else { return }
        let generation = UUID()
        historyGeneration = generation
        historyLoading = true
        defer { if historyGeneration == generation { historyLoading = false } }
        let offset = append ? nextOffset ?? 0 : 0
        let payload: [String: Any] = ["query": historyQuery, "archived": showTrash,
                                      "offset": offset, "limit": 30]
        guard let data = await model.performChatIPC("conversations.list", payload: payload),
              generation == historyGeneration, !Task.isCancelled else { return }
        do {
            let (entries, next) = try ChatConversationSummary.page(data)
            let existing = append ? conversations : []
            let ids = Set(existing.map(\.id))
            conversations = existing + entries.filter { !ids.contains($0.id) }
            nextOffset = next
        } catch { self.error = "No pude leer el historial. Inténtalo de nuevo." }
    }

    func open(_ conversation: ChatConversationSummary, model: MenuBarModel) async {
        guard model.canManageChatHistory, !conversation.archived else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard let data = await model.performChatIPC("conversations.messages", payload: [
            "conversation_id": conversation.id.uuidString.lowercased()
        ]) else { return }
        do {
            let page = try ChatHistoryPage(data)
            guard page.conversationID == conversation.id else { throw LocalIPCError.malformedResponse }
            resetConversation()
            conversationID = conversation.id
            transcript.restore(page.messages)
            nextBeforeSequence = page.nextBeforeSequence
        } catch { self.error = "No pude abrir esa conversación. Inténtalo de nuevo." }
    }

    func loadOlder(model: MenuBarModel) async {
        guard model.canManageChatHistory, let id = conversationID,
              let before = nextBeforeSequence else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard let data = await model.performChatIPC("conversations.messages", payload: [
            "conversation_id": id.uuidString.lowercased(), "before_sequence": before
        ]) else { return }
        do {
            let page = try ChatHistoryPage(data)
            guard page.conversationID == id, conversationID == id else { return }
            transcript.prepend(page.messages)
            nextBeforeSequence = page.nextBeforeSequence
        } catch { self.error = "No pude cargar los mensajes anteriores." }
    }

    func setTrashed(_ conversation: ChatConversationSummary, model: MenuBarModel, trashed: Bool) async {
        guard model.canManageChatHistory else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard await model.performChatIPC(trashed ? "conversations.trash" : "conversations.restore",
                                        payload: ["conversation_id": conversation.id.uuidString.lowercased()]) != nil
        else { return }
        if trashed && conversationID == conversation.id { resetConversation() }
        await refreshHistory(model: model)
    }

    func addFiles(_ urls: [URL]) async {
        guard !isEditing else { return }
        importing = true
        error = nil
        defer { importing = false }
        let existing = attachments
        let result = await Task.detached(priority: .userInitiated) { () -> ([ChatAttachment]?, String?) in
            do {
                guard urls.count + existing.count <= 4 else { throw ChatAttachmentError.tooMany }
                let added = try urls.map { try ChatAttachmentLoader.load($0) }
                let combined = existing + added
                try ChatAttachmentLoader.validate(combined)
                return (combined, nil)
            } catch { return (nil, error.localizedDescription) }
        }.value
        if let files = result.0 { attachments = files }
        else { error = result.1 ?? "No pude adjuntar esos archivos." }
    }
}
