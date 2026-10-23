import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

private let approvalLogger = Logger(subsystem: "ai.aegis.menubar", category: "Approvals")

// Behavior for Approvals; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func approvePending(expected: PendingApproval? = nil, always: Bool = false,
                        appScope: String? = nil) async {
        let requestedScope: String = switch appScope {
        case "conversation": "conversation"
        case "always": "always"
        case nil: always ? "always" : "once"
        default: "invalid"
        }
        approvalLogger.info("approval_attempt scope=\(requestedScope, privacy: .public)")
        guard
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit,
            !approvalActionInProgress,
            let pendingApproval,
            expected == nil || pendingApproval == expected,
            !always || pendingApproval.confirmation.standingGrantAvailable,
            appScope == nil || pendingApproval.confirmation.appGrantBundle != nil,
            let secret = ipcSecret
        else {
            let stale = expected != nil && self.pendingApproval != expected
            let reason = stale ? "stale" : "guard"
            let gateAvailable = userSessionExecutionGate.permit != nil
            approvalLogger.info("approval_rejected scope=\(requestedScope, privacy: .public) reason=\(reason, privacy: .public) session=\(self.userSessionAvailable, privacy: .public) gate=\(gateAvailable, privacy: .public) expected=\(expected != nil, privacy: .public) in_progress=\(self.approvalActionInProgress, privacy: .public)")
            if userSessionAvailable, userSessionExecutionGate.permit != nil,
               let appScope, appScope == "conversation" || appScope == "always",
               let expected, self.pendingApproval != expected {
                // Touch ID or voice may have consumed the one-shot approval
                // while its app-permission menu was still open.
                chat.recordAppGrantResult(jobID: expected.jobID, scope: appScope,
                                          confirmed: false)
            }
            return
        }
        cancelDualAuthorization()
        approvalActionInProgress = true
        defer { approvalActionInProgress = false }
        let accepted = await Task.detached(priority: .userInitiated) {
            Self.approveJob(pendingApproval, always: always, appScope: appScope, secret: secret)
        }.value
        approvalLogger.info("approval_result scope=\(requestedScope, privacy: .public) accepted=\(accepted, privacy: .public)")
        guard userSessionExecutionGate.isCurrent(sessionPermit) else {
            approvalLogger.info("approval_rejected scope=\(requestedScope, privacy: .public) reason=session_changed")
            if accepted {
                _ = await Task.detached(priority: .userInitiated) {
                    Self.cancelJob(pendingApproval.jobID, secret: secret)
                }.value
            }
            return
        }
        if let appScope {
            chat.recordAppGrantResult(jobID: pendingApproval.jobID, scope: appScope,
                                      confirmed: accepted)
        }
        guard accepted else {
            let outcome = await awaitJob(pendingApproval.jobID, secret: secret)
            handleJobOutcome(outcome, jobID: pendingApproval.jobID)
            return
        }
        if pendingRecovery?.code == "correction_not_applied" { pendingRecovery = nil }
        cancelApprovalExpiry()
        self.pendingApproval = nil
        activeJobID = pendingApproval.jobID
        voiceState = .processing
        let outcome = await awaitJob(pendingApproval.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: pendingApproval.jobID)
        Task { await sendNextQueuedChatMessage() }
    }

    @discardableResult
    func denyPending(expected: PendingApproval? = nil) async -> Bool {
        guard
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit,
            !approvalActionInProgress,
            let pendingApproval,
            expected == nil || pendingApproval == expected,
            let secret = ipcSecret
        else {
            return false
        }
        cancelDualAuthorization()
        approvalActionInProgress = true
        defer { approvalActionInProgress = false }
        let denied = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(pendingApproval.jobID, secret: secret)
        }.value
        guard userSessionExecutionGate.isCurrent(sessionPermit) else { return false }
        guard denied else {
            logger.error("tool_confirmation_failed stage=deny")
            return false
        }
        logger.info(
            "tool_confirmation_denied tool=\(pendingApproval.confirmation.toolName, privacy: .public)"
        )
        if pendingRecovery?.code == "correction_not_applied" { pendingRecovery = nil }
        cancelApprovalExpiry()
        self.pendingApproval = nil
        if activeJobID == pendingApproval.jobID {
            activeJobID = nil
        }
        if activeComputerUseJobID == pendingApproval.jobID {
            activeComputerUseJobID = nil
        }
        JarvisPointerController.shared.hide()
        if chat.transcript.jobID == pendingApproval.jobID {
            chat.transcript.finish(String(localized: "No ejecuté la acción.", bundle: .module), jobID: pendingApproval.jobID, interrupted: true)
            voiceState = .completed
            scheduleWakeWordResume(if: wakeWordOptedIn)
            return true
        }
        voiceState = .speaking
        speakWithWakeWordIsolation(String(localized: "Entendido. No ejecuté la acción.", bundle: .module)) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
        }
        return true
    }

    func cancelActiveComputerUse() async {
        guard
            let jobID = activeComputerUseJobID,
            activeJobID == jobID,
            let secret = ipcSecret
        else {
            return
        }
        let cancelled = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(jobID, secret: secret)
        }.value
        guard cancelled, activeJobID == jobID else {
            logger.error("computer_control_cancel_failed")
            return
        }
        activeJobID = nil
        activeComputerUseJobID = nil
        cancelApprovalExpiry()
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        if chat.transcript.jobID == jobID {
            chat.transcript.finish(String(localized: "Control del Mac cancelado.", bundle: .module), jobID: jobID, interrupted: true)
        }
        voiceState = .idle
        logger.info("computer_control_cancelled")
    }

    func scheduleApprovalExpiry(for approval: PendingApproval) {
        cancelApprovalExpiry()
        let delay = LocalVoiceTurnFeedback.approvalExpiryDelay(
            expiresAt: approval.confirmation.expiresAt
        )
        approvalExpiryTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(Int64(delay * 1_000)))
            } catch {
                return
            }
            guard
                let self,
                !Task.isCancelled,
                userSessionAvailable,
                !approvalActionInProgress,
                pendingApproval == approval,
                activeJobID == approval.jobID,
                let secret = ipcSecret
            else {
                return
            }
            approvalActionInProgress = true
            defer { approvalActionInProgress = false }
            let outcome = await awaitJob(approval.jobID, secret: secret)
            guard pendingApproval == approval else { return }
            approvalExpiryTask = nil
            handleJobOutcome(outcome, jobID: approval.jobID)
        }
    }

    func cancelApprovalExpiry() {
        approvalExpiryTask?.cancel()
        approvalExpiryTask = nil
    }

    func beginDualAuthorization(for approval: PendingApproval) {
        cancelDualAuthorization()
        guard
            userSessionAvailable,
            let secret = ipcSecret
        else {
            return
        }
        // Without an owner the authorizer never opens the microphone and only asks for
        // Touch ID: voice stays off unless the owner enabled it, and the daemon only
        // lets a voice approve actions that read.
        let ownerIdentifier = Self.voiceApprovalOwner(
            effectiveSpeakerOwnerIdentifier,
            enabled: voiceApprovalEnabled,
            confirmation: approval.confirmation
        )
        let modelFingerprint = speakerIdentityModelFingerprint
        dualAuthorizationTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let result = await dualChannelAuthorizer.authorize(
                selectedOwnerIdentifier: ownerIdentifier,
                expectedModelFingerprint: modelFingerprint,
                physicalApproval: {
                    await Task.detached(priority: .userInitiated) {
                        Self.approveJob(approval, secret: secret)
                    }.value
                },
                voiceApproval: { evidence in
                    await Task.detached(priority: .userInitiated) {
                        Self.approveJobByVoice(
                            approval,
                            evidence: evidence,
                            secret: secret
                        )
                    }.value
                }
            )
            guard
                !Task.isCancelled,
                userSessionAvailable,
                pendingApproval == approval,
                activeJobID == approval.jobID
            else {
                return
            }
            dualAuthorizationTask = nil
            switch result {
            case .authorizedByTouchID, .authorizedByVoice:
                cancelApprovalExpiry()
                pendingApproval = nil
                withAnimation(.easeOut(duration: 0.15)) {
                    voiceState = .processing
                }
                logger.info(
                    "tool_confirmation_authorized channel=\(String(describing: result), privacy: .public)"
                )
                let outcome = await awaitJob(approval.jobID, secret: secret)
                handleJobOutcome(outcome, jobID: approval.jobID)
            case .manualConfirmationRequired:
                withAnimation(.easeOut(duration: 0.15)) {
                    voiceState = .awaitingAuthorization
                }
            }
        }
    }

    nonisolated static func voiceApprovalOwner(
        _ owner: String?,
        enabled: Bool,
        confirmation: IPCPendingConfirmation
    ) -> String? {
        enabled && confirmation.voiceApprovalAvailable ? owner : nil
    }

    func cancelDualAuthorization() {
        dualAuthorizationTask?.cancel()
        dualAuthorizationTask = nil
        dualChannelAuthorizer.cancel()
    }

    nonisolated private static func approveJob(
        _ approval: PendingApproval,
        always: Bool = false,
        appScope: String? = nil,
        secret: Data
    ) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).approveJob(
                approval.jobID,
                callDigest: approval.confirmation.callDigest,
                always: always,
                appScope: appScope
            ),
            let status = IPCJobStatusEvent(response: response),
            status.jobID == approval.jobID,
            status.state == .running
        else {
            return false
        }
        return true
    }

    nonisolated private static func approveJobByVoice(
        _ approval: PendingApproval,
        evidence: VoiceAuthorizationEvidence,
        secret: Data
    ) -> Bool {
        // The packaged daemon cannot run MLX Whisper, so the app sends what on-device
        // Speech heard during the approval window, signed together with the audio.
        let transcript = evidence.transcript
        guard
            let response = try? LocalIPCClient(secret: secret).approveJobByVoice(
                approval.jobID,
                callDigest: approval.confirmation.callDigest,
                pcmS16LE: evidence.pcmS16LE,
                speakerIdentifier: evidence.speakerIdentifier,
                speakerConfidence: evidence.speakerConfidence,
                ownerProfileMatch: evidence.ownerProfileMatch,
                transcript: transcript
            ),
            let status = IPCJobStatusEvent(response: response),
            status.jobID == approval.jobID,
            status.state == .running
        else {
            return false
        }
        return true
    }
}
