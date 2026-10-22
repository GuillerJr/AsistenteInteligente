import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Jobs; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func handleJobOutcome(_ outcome: JobOutcome, jobID: UUID) {
        guard activeJobID == jobID else { return }
        switch outcome {
        case let .completed(result):
            activeJobID = nil
            activeComputerUseJobID = nil
            cancelApprovalExpiry()
            cancelDualAuthorization()
            JarvisPointerController.shared.hide()
            if chat.transcript.jobID == jobID {
                chat.inputJobID = pendingInput?.id
                chat.transcript.finish(result, jobID: jobID)
                let revision = chat.transcriptRevision
                let persisted = chat.completedTurnPersisted
                Task {
                    await chat.synchronizeCompletion(persisted: persisted, revision: revision,
                        refreshRecent: { _ = await self.chat.loadRecent(model: self) },
                        refreshHistory: { await self.chat.refreshHistory(model: self) },
                        sendNext: { await self.sendNextQueuedChatMessage() })
                }
                voiceState = .completed
                scheduleWakeWordResume(if: wakeWordOptedIn)
            } else {
                if let id = voiceHistoryConversationID {
                    Task { await chat.refreshVoiceTranscript(conversationID: id, model: self) }
                }
                speakCompletedResult(result)
            }
        case let .awaitingConfirmation(confirmation):
            let approval = PendingApproval(jobID: jobID, confirmation: confirmation)
            pendingApproval = approval
            activeComputerUseJobID = confirmation.toolName == "computer_use" ? jobID : nil
            voiceState = .awaitingAuthorization
            scheduleApprovalExpiry(for: approval)
            logger.info(
                "tool_confirmation_requested tool=\(confirmation.toolName, privacy: .public)"
            )
            beginDualAuthorization(for: approval)
        case let .failed(errorCode):
            activeJobID = nil
            activeComputerUseJobID = nil
            cancelApprovalExpiry()
            cancelDualAuthorization()
            pendingApproval = nil
            if speechStreamOpen {
                speechOutput.stop()
                speechStreamChunker.reset()
                speechStreamOpen = false
            }
            JarvisPointerController.shared.hide()
            if chat.transcript.jobID == jobID {
                if errorCode != "job_cancelled" { chat.recordFailure(errorCode) }
                let message = errorCode == "job_cancelled" ? String(localized: "Respuesta cancelada.", bundle: .module)
                    : ChatRecovery.message(errorCode)
                chat.transcript.finish(message,
                                       jobID: jobID, interrupted: true)
                voiceState = .idle
                scheduleWakeWordResume(if: wakeWordOptedIn)
                Task { await sendNextQueuedChatMessage() }
                return
            }
            logger.error(
                "voice_turn_failed stage=job reason=\(errorCode, privacy: .public)"
            )
            announceVoiceFailure(
                LocalVoiceTurnFeedback.spokenJobFailure(for: errorCode),
                code: errorCode
            )
        }
    }

    func trackSubmission(
        _ submission: SubmissionOutcome,
        conversationDecision: LocalVoiceConversationSession.Decision,
        secret: Data
    ) async {
        voiceHistoryConversationID = nil
        if
            conversationDecision.persistAcceptedConversation,
            let acceptedConversationID = submission.conversationID
        {
            let now = Date()
            conversationID = acceptedConversationID
            conversationLastUsedAt = now
            conversationSpeakerID = conversationDecision.boundSpeakerID
            conversationModelFingerprint = conversationDecision.boundModelFingerprint
            if conversationDecision.boundSpeakerID != nil,
               conversationDecision.boundModelFingerprint != nil {
                voiceHistoryConversationID = acceptedConversationID
                chat.conversationID = acceptedConversationID
                chat.transcriptRevision = UUID()
            } else {
                voiceHistoryConversationID = nil
            }
            UserDefaults.standard.set(
                acceptedConversationID.uuidString.lowercased(),
                forKey: voiceConversationDefaultsKey
            )
            UserDefaults.standard.set(
                now.timeIntervalSince1970,
                forKey: voiceConversationLastUsedDefaultsKey
            )
            if let speakerID = conversationDecision.boundSpeakerID {
                UserDefaults.standard.set(speakerID, forKey: voiceConversationSpeakerDefaultsKey)
            } else {
                UserDefaults.standard.removeObject(forKey: voiceConversationSpeakerDefaultsKey)
            }
            if let modelFingerprint = conversationDecision.boundModelFingerprint {
                UserDefaults.standard.set(
                    modelFingerprint,
                    forKey: voiceConversationModelDefaultsKey
                )
            } else {
                UserDefaults.standard.removeObject(forKey: voiceConversationModelDefaultsKey)
            }
        }
        activeJobID = submission.jobID
        activeComputerUseJobID = nil
        voiceState = .processing
        speechStreamChunker.reset()
        speechStreamOpen = false
        enableInterruptionListening()
        let outcome = await awaitJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
    }

    func awaitJob(_ jobID: UUID, secret: Data) async -> JobOutcome {
        let startedAt = ProcessInfo.processInfo.systemUptime
        var monitoring = JobMonitoringDeadline(startedAt: startedAt)
        var observedStreamVersion = 0
        while !monitoring.isExpired(at: ProcessInfo.processInfo.systemUptime) {
            guard activeJobID == jobID else { return .failed("job_superseded") }
            let afterStreamVersion = observedStreamVersion
            let status = await Task.detached(priority: .utility) { @Sendable in
                Self.waitForJobChange(
                    jobID,
                    afterStreamVersion: afterStreamVersion,
                    secret: secret
                )
            }.value
            guard activeJobID == jobID else { return .failed("job_superseded") }
            guard let status, status.jobID == jobID else {
                return .failed("job_status_unavailable")
            }
            monitoring.observe(status, at: ProcessInfo.processInfo.systemUptime)
            if
                status.streamVersion > observedStreamVersion,
                let partial = status.partialResult
            {
                observedStreamVersion = status.streamVersion
                let valid = chat.transcript.jobID == jobID
                    ? chat.transcript.update(partial, jobID: jobID)
                    : consumeStreamSnapshot(partial)
                guard valid else {
                    logger.error("voice_turn_failed stage=stream_integrity")
                    _ = await Task.detached(priority: .userInitiated) {
                        Self.cancelJob(jobID, secret: secret)
                    }.value
                    return .failed("job_stream_invalid")
                }
            }
            switch status.state {
            case .completed:
                if chat.transcript.jobID == jobID {
                    chat.completedTurnPersisted = status.conversationPersisted == true
                }
                lastEvaluation = status.evaluation
                if let evaluation = status.evaluation {
                    logger.info(
                        "voice_turn_evaluated brain=\(evaluation.brain.rawValue, privacy: .public) active_latency_ms=\(evaluation.totalLatencyMilliseconds, privacy: .public) wall_latency_ms=\(evaluation.wallLatencyMilliseconds ?? evaluation.totalLatencyMilliseconds, privacy: .public) confirmation_wait_ms=\(evaluation.confirmationWaitMilliseconds, privacy: .public) first_partial_ms=\(evaluation.firstPartialLatencyMilliseconds ?? -1, privacy: .public) chunks=\(evaluation.streamChunks, privacy: .public) verified=\(evaluation.outcomeVerified, privacy: .public) owner_verified=\(evaluation.ownerVerified, privacy: .public)"
                    )
                    if evaluation.voiceRequest {
                        Task.detached(priority: .utility) {
                            try? await JarvisOperationalEvidenceRecorder.shared.recordVoiceTurn(
                                ownerVerified: evaluation.ownerVerified,
                                firstPartialMilliseconds:
                                    evaluation.firstPartialLatencyMilliseconds,
                                totalMilliseconds: evaluation.totalLatencyMilliseconds
                            )
                        }
                    }
                }
                guard let result = status.result else { return .failed("job_result_invalid") }
                if let request = status.inputRequest, request.expiresAt > Date() {
                    pendingInput = PendingInput(id: jobID, request: request)
                }
                return .completed(result)
            case .failed:
                lastEvaluation = status.evaluation
                return .failed(status.errorCode ?? "job_failed")
            case .cancelled:
                lastEvaluation = status.evaluation
                return .failed("job_cancelled")
            case .awaitingConfirmation:
                guard let confirmation = status.confirmation else {
                    return .failed("job_confirmation_invalid")
                }
                return .awaitingConfirmation(confirmation)
            case .queued, .running:
                continue
            }
        }
        if activeJobID == jobID {
            _ = await Task.detached(priority: .userInitiated) { Self.cancelJob(jobID, secret: secret) }.value
        }
        return .failed("job_timeout")
    }

    nonisolated static func submitRequest(
        _ request: SubmissionRequest,
        conversationID: UUID?,
        persistConversation: Bool,
        secret: Data
    ) -> SubmissionOutcome? {
        guard let client = try? LocalIPCClient(secret: secret) else {
            return nil
        }
        let response = switch request {
        case let .voice(
            transcript,
            preferredBrowserBundleIdentifier,
            activeApplicationBundleIdentifier
        ):
            try? client.submitVoiceTranscript(
                transcript,
                conversationID: conversationID,
                persistConversation: persistConversation,
                preferredBrowserBundleIdentifier: preferredBrowserBundleIdentifier,
                activeApplicationBundleIdentifier: activeApplicationBundleIdentifier
            )
        case let .image(image, transcript):
            try? client.submitImage(
                text: transcript.text,
                image: image,
                voiceContext: transcript,
                conversationID: conversationID,
                persistConversation: persistConversation
            )
        }
        guard
            let response,
            let submission = VoiceSubmissionEvent(response: response),
            !persistConversation || submission.conversationID != nil
        else {
            return nil
        }
        return SubmissionOutcome(
            jobID: submission.jobID,
            conversationID: submission.conversationID
        )
    }

    nonisolated private static func waitForJobChange(
        _ jobID: UUID,
        afterStreamVersion: Int,
        secret: Data
    ) -> IPCJobStatusEvent? {
        guard
            let response = try? LocalIPCClient(secret: secret).waitForJobChange(
                jobID,
                afterStreamVersion: afterStreamVersion
            )
        else {
            return nil
        }
        return IPCJobStatusEvent(response: response)
    }

    nonisolated static func cancelJob(_ jobID: UUID, secret: Data) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).cancelJob(jobID),
            let status = IPCJobStatusEvent(response: response),
            status.jobID == jobID,
            status.state == .cancelled
        else {
            return false
        }
        return true
    }

    struct SubmissionOutcome: Sendable {
        let jobID: UUID
        let conversationID: UUID?
    }

    enum SubmissionRequest: Sendable {
        case voice(
            SpeechTranscriptEvent,
            preferredBrowserBundleIdentifier: String?,
            activeApplicationBundleIdentifier: String?
        )
        case image(LocalImageAttachment, transcript: SpeechTranscriptEvent)
    }

    enum JobOutcome: Sendable {
        case completed(String)
        case awaitingConfirmation(IPCPendingConfirmation)
        case failed(String)
    }
}
