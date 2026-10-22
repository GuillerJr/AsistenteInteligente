import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Interactions; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func pauseApprovalForCorrection() {
        cancelDualAuthorization()
    }

    func resumeApprovalAfterCorrection(_ approval: PendingApproval) {
        guard pendingApproval == approval, userSessionAvailable,
              !approvalActionInProgress, !interactionSubmissionInProgress,
              pendingRecovery?.code != "correction_not_applied",
              approval.confirmation.expiresAt > Date() else { return }
        beginDualAuthorization(for: approval)
    }

    func correctPendingRequest(_ text: String, expectedApproval: PendingApproval) async {
        guard pendingApproval == expectedApproval, !interactionSubmissionInProgress,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= 4_096 else { return }
        let toChat = chat.transcript.jobID == expectedApproval.jobID
        guard await denyPending(expected: expectedApproval) else {
            guard userSessionAvailable, pendingApproval == expectedApproval else { return }
            cancelDualAuthorization()
            pendingRecovery = PendingRecovery(
                code: "correction_not_applied",
                message: String(localized: "No pude confirmar la cancelación de la acción anterior. La corrección no se envió; revisa el resultado antes de solicitar otro cambio.", bundle: .module)
            )
            return
        }
        guard pendingApproval == nil else { return }
        await submitInteractionText(text, toChat: toChat)
    }

    func answerPendingInput(_ answer: String, expectedID: UUID? = nil) async {
        guard let input = pendingInput, input.request.expiresAt > Date(),
              expectedID == nil || input.id == expectedID,
              !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              answer.utf8.count <= 4_096 else { return }
        let text = String(localized: "Solicitud del usuario: \(input.request.requestText)\n", bundle: .module)
            + String(localized: "Pregunta de aclaración: \(input.request.question)\n", bundle: .module)
            + String(localized: "Información adicional del usuario: \(answer)", bundle: .module)
        await submitInteractionText(text, toChat: chat.inputJobID == input.id)
    }

    private func submitInteractionText(_ text: String, toChat: Bool = false) async {
        if toChat, !chat.isBusy, pendingApproval == nil, activeJobID == nil {
            pendingInput = nil
            await sendChatMessage(text, maximumBytes: 16_000)
            return
        }
        guard userSessionAvailable, let permit = userSessionExecutionGate.permit,
              pendingApproval == nil, activeJobID == nil,
              !interactionSubmissionInProgress, let secret = ipcSecret,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= 16_000 else { return }
        interactionSubmissionInProgress = true
        defer { interactionSubmissionInProgress = false }
        cancelFollowUpWindow()
        speechOutput.stop()
        pendingInput = nil
        pendingRecovery = nil
        voiceState = .submitting
        let currentConversation = voiceHistoryConversationID ?? chat.conversationID
        mascotUsesVoice = true
        voiceInputText = String(text.prefix(4_096))
        voiceOutputText = nil
        let response = await Task.detached(priority: .utility) { () -> (SubmissionOutcome?, String?) in
            var payload: [String: Any] = ["text": text]
            if let currentConversation { payload["conversation_id"] = currentConversation.uuidString.lowercased() }
            guard let client = try? LocalIPCClient(secret: secret),
                  let response = try? client.call(method: "swarm.submit", payload: payload)
            else { return (nil, "daemon_unavailable") }
            guard response.ok else { return (nil, response.errorCode ?? "job_submission_failed") }
            guard let submission = VoiceSubmissionEvent(response: response)
            else { return (nil, "job_result_invalid") }
            return (SubmissionOutcome(jobID: submission.jobID, conversationID: submission.conversationID), nil)
        }.value
        let submission = response.0
        guard userSessionExecutionGate.isCurrent(permit) else {
            if let submission {
                _ = await Task.detached(priority: .utility) { Self.cancelJob(submission.jobID, secret: secret) }.value
            }
            voiceState = .idle
            return
        }
        guard let submission else {
            let code = response.1 ?? "daemon_unavailable"
            let message = code == "daemon_unavailable"
                ? String(localized: "No pude comunicarme con el servicio local.", bundle: .module)
                : LocalVoiceTurnFeedback.spokenJobFailure(for: code)
            announceVoiceFailure(message, code: code)
            return
        }
        if let currentConversation {
            voiceHistoryConversationID = currentConversation
        }
        activeJobID = submission.jobID
        voiceState = .processing
        speechStreamChunker.reset()
        speechStreamOpen = false
        let outcome = await awaitJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
    }

    func cancelInteraction(expectedID: String? = nil) async {
        guard expectedID == nil || interactionID == expectedID else { return }
        if pendingApproval != nil {
            await denyPending()
            return
        }
        if pendingBrowserSelection != nil {
            browserChoiceTask?.cancel()
            browserChoiceTask = nil
            speechOutput.stop()
            pendingBrowserSelection = nil
            voiceState = .idle
        }
        if pendingInput != nil {
            cancelFollowUpWindow()
            speechOutput.stop()
            pendingInput = nil
            voiceState = .idle
        }
        pendingRecovery = nil
    }

    func expireInput(_ id: UUID) {
        guard pendingInput?.id == id else { return }
        pendingInput = nil
        if !voiceState.isBusy { voiceState = .idle }
    }
}
