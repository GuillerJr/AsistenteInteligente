import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Chat; observable storage remains in MenuBarModel.
extension MenuBarModel {
    var canSendChatMessage: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact
            && hybridBrainReady && activeJobID == nil && pendingApproval == nil
            && pendingInput == nil && !voiceState.isBusy && !chat.isBusy
            && !speakerModelTrainingState.isBusy && !chat.selectionLoading && !chat.importing
    }

    var canManageChatHistory: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact
            && activeJobID == nil && !chat.isEditing && !chat.historyLoading && !voiceState.isBusy
            && pendingApproval == nil && pendingInput == nil
    }

    var canReadChatHistory: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact && ipcSecret != nil
    }

    func performChatIPC(_ method: String, payload: [String: Any] = [:]) async -> Data? {
        guard userSessionAvailable, let permit = userSessionExecutionGate.permit,
              let secret = ipcSecret,
              let encoded = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        let result = await Task.detached(priority: .userInitiated) { () -> (Data?, String?) in
            do {
                let decoded = try JSONSerialization.jsonObject(with: encoded) as? [String: Any] ?? [:]
                let client = try LocalIPCClient(secret: secret, timeoutSeconds:
                    JarvisScheduledOperation.timeout(method, fallback: 5))
                let response = try client.call(method: method, payload: decoded)
                guard response.ok else { return (nil, response.errorCode) }
                return (try JSONSerialization.data(withJSONObject: response.payload), nil)
            } catch { return (nil, "daemon_unavailable") }
        }.value
        guard userSessionExecutionGate.isCurrent(permit), !Task.isCancelled else { return nil }
        if result.0 == nil {
            if let message = JarvisScheduledOperation.errorMessage(result.1) {
                chat.error = message
                return nil
            }
            switch result.1 {
            case "conversation_not_found", "telegram_conversation_not_found":
                chat.error = String(localized: "Esa conversación ya no está disponible. Actualiza el historial.", bundle: .module)
            case "telegram_owner_not_linked":
                chat.error = String(localized: "Vincula y habilita tu cuenta en Configuración → Telegram.", bundle: .module)
            case "telegram_conversation_busy":
                chat.error = String(localized: "Telegram tiene una tarea activa. Termínala o cancélala antes de cambiar de chat.", bundle: .module)
            default:
                chat.error = String(localized: "No pude completar la operación. Inténtalo de nuevo.", bundle: .module)
            }
        }
        return result.0
    }

    var canQueueChatMessage: Bool {
        chat.transcript.jobID != nil && queuedChatMessages.count < Self.maximumQueuedChatMessages
            && chat.attachments.isEmpty && !chat.importing
    }

    @discardableResult
    func queueChatMessage(_ text: String) -> Bool {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canQueueChatMessage, !cleaned.isEmpty, cleaned.utf8.count <= 4_096 else { return false }
        queuedChatMessages.append(cleaned)
        chat.consumeSubmittedDraft(cleaned)
        logger.info("chat_message_queued count=\(self.queuedChatMessages.count, privacy: .public)")
        return true
    }

    func removeQueuedChatMessage(at index: Int) {
        guard queuedChatMessages.indices.contains(index) else { return }
        queuedChatMessages.remove(at: index)
    }

    /// Redirect the running request (Codex's "steer"): stop it and ask again with the
    /// owner's correction attached, so Jarvis does not finish the wrong thing first.
    @discardableResult
    func steerChatResponse(_ correction: String) async -> Bool {
        await steerChatResponse(correction, cancel: { await self.cancelChatResponse() },
                                send: {
            await self.submitChatMessage(
                .init(text: $0, attachments: self.chat.attachments, planMode: self.chat.planMode),
                consumingComposerText: correction)
        })
    }

    @discardableResult
    func steerChatResponse(_ correction: String, cancel: () async -> Void,
                           send: (String) async -> Bool) async -> Bool {
        let cleaned = correction.trimmingCharacters(in: .whitespacesAndNewlines)
        guard chat.transcript.jobID != nil, !cleaned.isEmpty,
              let original = chat.transcript.messages.last(where: { $0.role == .user })?.text
        else { return false }
        let conversationID = chat.conversationID
        let revision = chat.transcriptRevision
        await cancel()
        guard chat.conversationID == conversationID, chat.transcriptRevision == revision,
              chat.transcript.jobID == nil else { return false }
        let combined = String(String(localized: "\(original)\n\n(Corrección: \(cleaned))", bundle: .module).prefix(4_000))
        logger.info("chat_response_steered")
        return await send(combined)
    }

    func sendNextQueuedChatMessage() async {
        await sendNextQueuedChatMessage(send: { await self.sendChatMessage($0) })
    }

    func sendNextQueuedChatMessage(send: (String) async -> Bool) async {
        guard !queuedChatMessages.isEmpty, canSendChatMessage else { return }
        let next = queuedChatMessages.removeFirst()
        if !(await send(next)) { queuedChatMessages.insert(next, at: 0) }
    }

    @discardableResult
    func sendChatMessage(_ text: String, maximumBytes: Int = 4_096,
                         consumingComposer: Bool = false) async -> Bool {
        await sendChatMessage(text, consumingComposer: consumingComposer) { submission, consumed in
            await self.submitChatMessage(submission, maximumBytes: maximumBytes,
                                         consumingComposerText: consumed)
        }
    }

    @discardableResult
    func sendChatMessage(_ text: String, consumingComposer: Bool = false,
                         send: (ChatSubmission, String?) async -> Bool) async -> Bool {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Automatic turns own only their text. Callers with their own files must
        // provide an explicit ChatSubmission instead of borrowing the composer.
        let attachments = consumingComposer ? chat.attachments : []
        let text = cleaned.isEmpty && !attachments.isEmpty ? String(localized: "Analiza los archivos adjuntos.", bundle: .module) : cleaned
        return await send(.init(text: text, attachments: attachments, planMode: chat.planMode),
                          consumingComposer ? cleaned : nil)
    }

    @discardableResult
    func submitChatMessage(_ submission: ChatSubmission, maximumBytes: Int = 4_096,
                           consumingComposerText: String? = nil) async -> Bool {
        let text = submission.text
        let attachments = submission.attachments
        guard canSendChatMessage, !text.isEmpty, text.utf8.count <= maximumBytes,
              let permit = userSessionExecutionGate.permit, let secret = ipcSecret else { return false }
        let conversationID = chat.conversationID
        let revision = chat.transcriptRevision
        if chat.transcript.hasNewerHistory
            || chat.transcript.messages.count + 2 > TextChatTranscript.maximumVisibleMessages {
            guard await chat.loadRecent(model: self), canSendChatMessage,
                  chat.conversationID == conversationID, chat.transcriptRevision == revision,
                  userSessionExecutionGate.isCurrent(permit) else { return false }
        }
        chat.submitting = true
        defer { chat.submitting = false }
        chat.transcriptRevision = UUID()
        mascotUsesVoice = false
        voiceInputText = nil
        voiceOutputText = nil
        voiceHistoryConversationID = nil
        mascotNotice = nil
        chat.error = nil
        chat.appGrantWarning = nil
        guard chat.beginSubmission(submission, consumingComposerText: consumingComposerText)
        else { return false }
        chat.activeSubmission = submission
        chat.completedTurnPersisted = false
        chat.retrySubmission = nil
        if submission.planMode, let reply = chat.transcript.messages.last?.id { chat.planReplies.insert(reply) }
        cancelFollowUpWindow()
        speechOutput.stop()
        speechStreamChunker.reset()
        speechStreamOpen = false
        pendingRecovery = nil
        lastVoiceFailureCode = nil
        _ = pauseWakeWordListening()
        voiceState = .submitting
        let existingConversation = chat.conversationID
        let temporaryChat = chat.temporary
        let projectID = existingConversation == nil && !temporaryChat ? chat.newChatProjectID : nil
        let result = await Task.detached(priority: .userInitiated) { () -> (UUID?, VoiceSubmissionEvent?, String?) in
            do {
                let client = try LocalIPCClient(secret: secret)
                var conversation = existingConversation
                if conversation == nil {
                    let title = String(text.replacingOccurrences(of: "\n", with: " ").prefix(80))
                    let created = try client.call(
                        method: "conversations.create",
                        payload: temporaryChat ? ["title": String(localized: "Chat temporal", bundle: .module), "temporary": true]
                                               : projectID.map { ["title": title, "project_id": $0] }
                                                 ?? ["title": title]
                    )
                    guard created.ok, let raw = created.payload["conversation_id"] as? String,
                          let parsed = UUID(uuidString: raw) else {
                        return (nil, nil, created.errorCode ?? "conversation_unavailable")
                    }
                    conversation = parsed
                }
                guard let conversation else { return (nil, nil, "conversation_unavailable") }
                let response = try client.call(method: submission.method,
                                               payload: submission.payload(conversationID: conversation))
                return (conversation, VoiceSubmissionEvent(response: response),
                        response.ok ? nil : response.errorCode ?? "job_submission_failed")
            } catch { return (existingConversation, nil, "daemon_unavailable") }
        }.value
        guard userSessionExecutionGate.isCurrent(permit) else {
            if let submission = result.1 {
                _ = await Task.detached(priority: .utility) { Self.cancelJob(submission.jobID, secret: secret) }.value
            }
            chat.transcript.finish(String(localized: "La sesión se bloqueó. La solicitud se canceló.", bundle: .module), interrupted: true)
            voiceState = .idle
            return true
        }
        // Submission only keeps the existing ID or adopts the ID just created
        // for this same chat. Preserve any next draft typed while IPC was pending.
        chat.conversationID = result.0
        if let created = result.0, temporaryChat { chat.adoptTemporaryConversation(created) }
        guard let submission = result.1 else {
            let code = result.2 ?? "job_submission_failed"
            chat.recordFailure(code)
            chat.transcript.finish(ChatRecovery.message(code), interrupted: true)
            voiceState = .idle
            scheduleWakeWordResume(if: wakeWordOptedIn)
            return true
        }
        chat.transcript.attach(submission.jobID)
        chat.consumeSubmittedAttachments(attachments)
        activeJobID = submission.jobID
        voiceState = .processing
        let outcome = await awaitJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
        return true
    }

    func retryChatMessage() async {
        await retryChatMessage(refresh: { await self.refreshDaemon() },
                               send: { await self.submitChatMessage($0) })
    }

    func retryChatMessage(refresh: () async -> Void, send: (ChatSubmission) async -> Bool) async {
        guard chat.retryAvailable, let retry = chat.retrySubmission else { return }
        let conversationID = chat.conversationID
        let revision = chat.transcriptRevision
        await refresh()
        guard canSendChatMessage, chat.conversationID == conversationID,
              chat.transcriptRevision == revision else { return }
        _ = await send(retry)
    }

    func cancelChatResponse() async {
        guard !chat.cancelling, userSessionAvailable, let jobID = chat.transcript.jobID,
              activeJobID == jobID, let secret = ipcSecret else { return }
        chat.cancelling = true
        defer { chat.cancelling = false }
        if pendingApproval?.jobID == jobID { _ = await denyPending(); return }
        let cancelled = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(jobID, secret: secret)
        }.value
        guard cancelled, activeJobID == jobID else { return }
        chat.transcript.finish(String(localized: "Respuesta cancelada.", bundle: .module), jobID: jobID, interrupted: true)
        activeJobID = nil
        activeComputerUseJobID = nil
        voiceState = .idle
        scheduleWakeWordResume(if: wakeWordOptedIn)
    }
}
