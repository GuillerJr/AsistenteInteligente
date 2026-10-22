import AegisAudioCore
import Foundation

extension JarvisChatStore {
    /// Reconcile persisted identities before releasing the next queued turn. A
    /// response the daemon intentionally did not save must remain visible locally.
    func synchronizeCompletion(persisted: Bool, revision: UUID,
                               refreshRecent: () async -> Void,
                               refreshHistory: () async -> Void,
                               sendNext: () async -> Void) async {
        guard transcriptRevision == revision, !isBusy else { return }
        if persisted { await refreshRecent() }
        await refreshHistory()
        guard transcriptRevision == revision, !isBusy else { return }
        await sendNext()
    }

    /// Only persisted, complete messages are actionable. Provisional IDs are replaced
    /// after the job finishes, before a button can address a daemon-owned turn.
    func replayTarget(for message: TextChatTranscript.Message) -> TextChatTranscript.Message? {
        guard message.sequence != nil, !message.truncated, !message.interrupted else { return nil }
        if message.role == .user { return message }
        guard message.id == transcript.messages.last?.id,
              let index = transcript.messages.firstIndex(where: { $0.id == message.id }), index > 0 else { return nil }
        let user = transcript.messages[index - 1]
        guard user.role == .user, user.sequence.map({ $0 + 1 == message.sequence }) == true else { return nil }
        return user
    }

    func replay(_ message: TextChatTranscript.Message, regenerate: Bool, model: MenuBarModel) async {
        guard model.canManageChatHistory, let source = conversationID,
              let target = replayTarget(for: message) else { return }
        let revision = transcriptRevision
        let wasTemporary = temporary
        selectionLoading = true
        guard let data = await model.performChatIPC("conversations.fork", payload: [
            "conversation_id": source.uuidString.lowercased(),
            "before_turn_id": target.id.uuidString.lowercased()
        ]), conversationID == source, transcriptRevision == revision else {
            selectionLoading = false; return
        }
        do {
            let replay = try ChatReplayDraft(data)
            guard replay.conversation.id != source else { throw LocalIPCError.malformedResponse }
            selectionLoading = false
            await open(replay.conversation, model: model)
            guard conversationID == replay.conversation.id else { return }
            temporary = wasTemporary
            if wasTemporary { adoptTemporaryConversation(replay.conversation.id) }
            attachments = replay.attachments
            if let name = replay.missingImage {
                notice = String(localized: "Adjunta de nuevo «\(name)» antes de enviar. Las imágenes originales no se guardan.", bundle: .module)
            } else {
                notice = String(localized: "Se creó una bifurcación desde este mensaje. El chat original se conserva.", bundle: .module)
            }
            if regenerate && replay.missingImage == nil && replay.text.utf8.count <= 4_096 {
                let submission = ChatSubmission(text: replay.text, attachments: replay.attachments,
                                                planMode: planMode)
                await sendReplayPreservingDraft(replay.text) { await model.submitChatMessage(submission) }
            } else {
                composerDraft = replay.text
            }
            await refreshHistory(model: model)
        } catch {
            selectionLoading = false
            self.error = String(localized: "No pude preparar el mensaje para editarlo. Inténtalo de nuevo.", bundle: .module)
        }
    }

    func sendReplayPreservingDraft(_ text: String, send: () async -> Bool) async {
        let id = conversationID
        let revision = transcriptRevision
        guard !(await send()), conversationID == id, transcriptRevision == revision,
              composerDraft?.isEmpty != false else { return }
        composerDraft = text
    }

    func rate(_ message: TextChatTranscript.Message, value: String, model: MenuBarModel) async {
        guard model.canManageChatHistory, let id = conversationID, message.sequence != nil,
              message.role == .assistant, !message.interrupted,
              ["helpful", "unhelpful"].contains(value), !feedbackUpdating.contains(message.id) else { return }
        let revision = transcriptRevision
        let selected: String? = message.feedback == value ? nil : value
        feedbackUpdating.insert(message.id)
        defer { feedbackUpdating.remove(message.id) }
        guard let data = await model.performChatIPC("conversations.feedback", payload: [
            "conversation_id": id.uuidString.lowercased(), "turn_id": message.id.uuidString.lowercased(),
            "value": selected as Any? ?? NSNull()
        ]), conversationID == id, transcriptRevision == revision,
              let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              response["turn_id"] as? String == message.id.uuidString.lowercased(),
              response["value"] as? String == selected else { return }
        transcript.setFeedback(selected, messageID: message.id)
    }

    func setFiled(_ conversation: ChatConversationSummary, model: MenuBarModel, filed: Bool) async {
        guard model.canManageChatHistory else { return }
        selectionLoading = true
        defer { selectionLoading = false }
        guard await model.performChatIPC("conversations.file", payload: [
            "conversation_id": conversation.id.uuidString.lowercased(), "filed": filed
        ]) != nil else { return }
        if conversationID == conversation.id {
            selectedConversation?.filed = filed
            if filed { resetConversation() }
        }
        await refreshHistory(model: model)
    }

    func recordFailure(_ code: String) {
        error = ChatRecovery.message(code)
        retrySubmission = ChatRecovery.canRetry(code) ? activeSubmission : nil
        retryConversationID = conversationID
    }

    /// A completed action does not prove that its reusable app permission was saved.
    /// Only the signed scope-specific response can produce this receipt.
    func recordAppGrantResult(jobID: UUID, scope: String, confirmed: Bool) {
        guard transcript.jobID == jobID, scope == "conversation" || scope == "always" else { return }
        guard confirmed else {
            notice = nil
            appGrantWarning = String(localized: "No pude confirmar que se guardara el permiso para esta app. Revisa la aprobación antes de volver a intentarlo.", bundle: .module)
            return
        }
        appGrantWarning = nil
        notice = scope == "conversation"
            ? String(localized: "Permiso guardado para esta conversación.", bundle: .module)
            : String(localized: "Permiso permanente guardado. Puedes quitarlo en Ajustes → Memoria y privacidad.", bundle: .module)
    }
}
