import AegisAudioCore
import Foundation

extension JarvisChatStore {
    func refreshHistory(model: MenuBarModel, append: Bool = false) async {
        guard model.canReadChatHistory else { return }
        await refreshHistory(append: append) { method, payload in
            await model.performChatIPC(method, payload: payload, reportError: false)
        }
    }

    func open(_ conversation: ChatConversationSummary, model: MenuBarModel) async {
        guard !Task.isCancelled, model.canManageChatHistory, !conversation.archived else { return }
        pane = .chat
        newChatProjectID = nil
        selectionLoading = true
        let requestedSearchGeneration = historyGeneration
        let openingSearchResult = !historyCriteria.query.isEmpty || conversation.searchMatch != nil
        defer { selectionLoading = false }
        var payload: [String: Any] = ["conversation_id": conversation.id.uuidString.lowercased()]
        if let match = conversation.searchMatch { payload["before_sequence"] = match.sequence + 1 }
        let data = await model.performChatIPC("conversations.messages", payload: payload,
                                              reportError: !openingSearchResult)
        guard !Task.isCancelled,
              !openingSearchResult || historyGeneration == requestedSearchGeneration else { return }
        do {
            guard let data else { throw LocalIPCError.malformedResponse }
            let page = try ChatHistoryPage(data)
            try restoreConversation(conversation, page: page)
        } catch { self.error = String(localized: "No pude abrir esa conversación. Inténtalo de nuevo.", bundle: .module) }
    }

    /// Apply a validated history response in one synchronous transition, so the
    /// outgoing composer is retained before the incoming draft is restored.
    func restoreConversation(_ conversation: ChatConversationSummary, page: ChatHistoryPage) throws {
        guard !conversation.archived, page.conversationID == conversation.id else {
            throw LocalIPCError.malformedResponse
        }
        if let match = conversation.searchMatch {
            guard page.messages.contains(where: { $0.id == match.turnID && $0.sequence == match.sequence })
            else { throw LocalIPCError.malformedResponse }
        }
        switchConversation(to: conversation.id)
        selectedConversation = conversation
        transcript.restore(page.messages, hasNewerHistory: conversation.searchMatch != nil)
        nextBeforeSequence = page.nextBeforeSequence
        if let match = conversation.searchMatch {
            historyMatchNavigation = HistoryMatchNavigation(
                conversationID: conversation.id, turnID: match.turnID, sequence: match.sequence,
                transcriptRevision: transcriptRevision, searchGeneration: historyGeneration
            )
        }
    }

    func loadOlder(model: MenuBarModel) async {
        guard model.canManageChatHistory, let id = conversationID,
              let before = nextBeforeSequence else { return }
        historyMatchNavigation = nil
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
        } catch { self.error = String(localized: "No pude cargar los mensajes anteriores.", bundle: .module) }
    }

    @discardableResult
    func loadRecent(model: MenuBarModel) async -> Bool {
        guard model.canManageChatHistory, let id = conversationID else { return false }
        let revision = transcriptRevision
        selectionLoading = true
        defer { selectionLoading = false }
        guard let data = await model.performChatIPC("conversations.messages", payload: [
            "conversation_id": id.uuidString.lowercased()
        ]), conversationID == id, transcriptRevision == revision else { return false }
        do {
            let page = try ChatHistoryPage(data)
            guard page.conversationID == id else { throw LocalIPCError.malformedResponse }
            // Replace provisional IDs with persisted turn IDs, carrying the plan markers.
            let wasPlan = transcript.messages.last.map { planReplies.contains($0.id) } ?? false
            let wasImplemented = transcript.messages.last.map { implementedPlans.contains($0.id) } ?? false
            transcript.restore(page.messages)
            historyMatchNavigation = nil
            if wasPlan, let reply = page.messages.last?.id { planReplies.insert(reply) }
            if wasImplemented, let reply = page.messages.last?.id { implementedPlans.insert(reply) }
            nextBeforeSequence = page.nextBeforeSequence
            return true
        } catch {
            self.error = String(localized: "No pude volver a los mensajes recientes. Inténtalo de nuevo.", bundle: .module)
            return false
        }
    }

    func setTrashed(_ conversation: ChatConversationSummary, model: MenuBarModel, trashed: Bool) async {
        guard model.canManageChatHistory else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard await model.performChatIPC(trashed ? "conversations.trash" : "conversations.restore",
                                        payload: ["conversation_id": conversation.id.uuidString.lowercased()]) != nil
        else { return }
        if trashed {
            discardDraft(for: conversation.id)
            if conversationID == conversation.id { resetConversation(preservingDraft: false) }
        }
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
        else { error = result.1 ?? String(localized: "No pude adjuntar esos archivos.", bundle: .module) }
    }

    func rename(_ entry: ChatConversationSummary, title: String, model: MenuBarModel) async {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard model.canManageChatHistory, !entry.archived, !title.isEmpty, title.count <= 128 else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard let data = await model.performChatIPC("conversations.rename", payload: [
            "conversation_id": entry.id.uuidString.lowercased(), "title": title
        ]) else { return }
        do {
            let updated = try ChatConversationSummary.record(data)
            guard updated.id == entry.id else { throw LocalIPCError.malformedResponse }
            if conversationID == entry.id { selectedConversation = updated }
            await refreshHistory(model: model)
        } catch { self.error = String(localized: "No pude renombrar esa conversación.", bundle: .module) }
    }

    func fork(_ entry: ChatConversationSummary, model: MenuBarModel) async {
        guard model.canManageChatHistory, !entry.archived else { return }
        selectionLoading = true
        guard let data = await model.performChatIPC("conversations.fork", payload: [
            "conversation_id": entry.id.uuidString.lowercased()
        ]) else { selectionLoading = false; return }
        do {
            let fork = try ChatConversationSummary.record(data)
            guard fork.id != entry.id, !fork.archived else { throw LocalIPCError.malformedResponse }
            selectionLoading = false
            await open(fork, model: model)
            historyQuery = ""
            showTrash = false
            await refreshHistory(model: model)
        } catch {
            selectionLoading = false
            self.error = String(localized: "No pude crear la bifurcación. Inténtalo de nuevo.", bundle: .module)
        }
    }

    func continueInTelegram(_ entry: ChatConversationSummary, model: MenuBarModel) async {
        guard model.canManageChatHistory, !entry.archived else { return }
        selectionLoading = true
        guard await model.performChatIPC("telegram.conversation.select", payload: [
            "conversation_id": entry.id.uuidString.lowercased(), "confirm": true
        ]) != nil else { selectionLoading = false; return }
        selectionLoading = false
        error = nil
        await open(entry, model: model)
        notice = String(localized: "Telegram continuará este chat con tu siguiente mensaje.", bundle: .module)
    }

    func emptyTrash(model: MenuBarModel) async {
        guard model.canManageChatHistory else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard await model.performChatIPC("conversations.empty_trash") != nil else { return }
        for entry in conversations where entry.archived { discardDraft(for: entry.id) }
        await refreshHistory(model: model)
    }
}
