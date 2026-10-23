import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Voice; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func voiceHoldStarted() {
        pushToTalkHeld = true
        activeTranscriber?.setHeld(true)
    }

    func voiceHoldEnded() {
        pushToTalkHeld = false
        activeTranscriber?.setHeld(false)
        activeTranscriber?.finishNow()
    }

    var canStartVoiceTurn: Bool {
        !meetingRecording
            && userSessionAvailable
            && daemonState == .online
            && securityState == .intact
            && hybridBrainReady
            && voiceOutputState == .ready
            && microphonePermission == .authorized
            && speechPermission == .authorized
            && !voiceState.isBusy
            && !chat.isBusy && !chat.selectionLoading && !chat.importing
            && !speakerModelTrainingState.isBusy
            && pendingApproval == nil
    }

    var canStartScreenTurn: Bool {
        canStartVoiceTurn && screenCaptureAuthorized
    }

    func startVoiceTurn() async {
        guard !chat.isBusy else { return }
        if voiceState == .speaking || voiceState == .processing || voiceState == .submitting {
            await interruptCurrentTurnAndListen(source: .hotKey)
            return
        }
        if voiceState == .followingUp {
            cancelFollowUpWindow()
            voiceState = .idle
        }
        await performVoiceTurn(playCue: true)
    }

    func performVoiceTurn(
        playCue: Bool,
        admittedFromFollowUp: Bool = false
    ) async {
        let stateAllowsStart = admittedFromFollowUp
            ? voiceState == .listening
            : !voiceState.isBusy
        guard stateAllowsStart, pendingApproval == nil else {
            return
        }
        cancelFollowUpWindow()
        voiceActivityLevel = 0
        lastVoiceFailureCode = nil
        pendingRecovery = nil
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard
            canStartVoiceTurn,
            let secret = ipcSecret
        else {
            logger.error(
                "voice_turn_failed stage=preflight daemon=\(self.daemonState.title, privacy: .public) microphone=\(self.microphonePermission.rawValue, privacy: .public) speech=\(self.speechPermission.rawValue, privacy: .public)"
            )
            announceVoiceFailure(
                preflightFailureMessage(),
                code: "voice_preflight_unavailable"
            )
            return
        }

        logger.info("voice_turn_started")
        let capture = await captureSpokenPrompt(playCue: playCue)
        guard userSessionAvailable else {
            voiceState = .idle
            return
        }
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture {
                logger.error(
                    "voice_turn_failed stage=capture reason=\(reason.rawValue, privacy: .public)"
                )
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenCaptureFailure(for: reason.rawValue),
                    code: reason.rawValue
                )
            }
            return
        }
        lastSpeakerID = transcript.speakerID
        if pendingInput != nil || pendingBrowserSelection != nil {
            guard let trusted = await transcriptWithOwnerPresence(transcript),
                  trusted.ownerSpeakerProfile, trusted.ownerPresenceVerified, userSessionAvailable else {
                speakLocalVoiceUtility(String(localized: "No pude verificar al propietario. Responde desde el diálogo de Jarvis.", bundle: .module),
                                      utility: "conversation", event: "unverified")
                return
            }
        }
        if pendingInput != nil {
            await answerPendingInput(transcript.text)
            return
        }
        if pendingBrowserSelection != nil {
            if let selection = matchingBrowserOption(for: transcript.text) {
                await selectBrowser(bundleIdentifier: selection.bundleIdentifier)
            } else if ["cancelar", "cancela", "cancel"].contains(
                transcript.text.folding(
                    options: [.caseInsensitive, .diacriticInsensitive],
                    locale: Locale(identifier: "es")
                ).lowercased()
            ) {
                browserChoiceTask?.cancel()
                browserChoiceTask = nil
                pendingBrowserSelection = nil
                voiceState = .idle
            } else {
                voiceState = .awaitingAuthorization
            }
            return
        }
        if await handleLocalVoiceReplay(transcript) {
            return
        }
        if await handleLocalVoiceConversation(transcript) {
            return
        }
        if handleLocalVoiceCapabilities(transcript.text) {
            return
        }
        if handleLocalVoiceSpeakerIdentity(transcript.text, speakerID: transcript.speakerID) {
            return
        }
        if handleLocalVoiceApplicationContext(transcript.text) {
            return
        }
        if handleLocalVoiceTimer(transcript.text) {
            return
        }
        await submitVoiceTranscript(transcript, secret: secret)
    }

    func startImageVoiceTurn(fileURL: URL) async {
        guard !voiceState.isBusy, pendingApproval == nil else {
            return
        }
        voiceActivityLevel = 0
        lastVoiceFailureCode = nil
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartVoiceTurn, let secret = ipcSecret else {
            logger.error("image_turn_failed stage=preflight")
            announceVoiceFailure(
                preflightFailureMessage(),
                code: "voice_preflight_unavailable"
            )
            return
        }

        voiceState = .submitting
        let image = await Task.detached(priority: .userInitiated) {
            let accessed = fileURL.startAccessingSecurityScopedResource()
            defer {
                if accessed {
                    fileURL.stopAccessingSecurityScopedResource()
                }
            }
            return try? LocalImageEncoder.encodeFile(at: fileURL)
        }.value
        guard let image else {
            logger.error("image_turn_failed stage=encode")
            announceVoiceFailure(
                String(localized: "No pude leer esa imagen de forma segura.", bundle: .module),
                code: "image_encoding_failed"
            )
            return
        }

        await startVisualVoiceTurn(image, secret: secret)
    }

    func startScreenVoiceTurn() async {
        guard !voiceState.isBusy, pendingApproval == nil else {
            return
        }
        voiceActivityLevel = 0
        lastVoiceFailureCode = nil
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartScreenTurn, let secret = ipcSecret else {
            logger.error("screen_turn_failed stage=preflight")
            announceVoiceFailure(
                preflightFailureMessage(screenCaptureRequired: true),
                code: "voice_preflight_unavailable"
            )
            return
        }

        voiceState = .submitting
        guard let allowedBundleIdentifier = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        else {
            logger.error("screen_turn_failed stage=target")
            announceVoiceFailure(
                String(localized: "No pude identificar una ventana autorizada.", bundle: .module),
                code: "screen_target_unavailable"
            )
            return
        }
        let image: LocalImageAttachment
        let captureStartedAt = ProcessInfo.processInfo.systemUptime
        do {
            image = try await ScreenCaptureService.captureAuthorizedFrontmostWindow(
                allowedBundleIdentifier: allowedBundleIdentifier
            )
            let captureMilliseconds = max(
                0,
                Int(
                    ((ProcessInfo.processInfo.systemUptime - captureStartedAt) * 1_000)
                        .rounded()
                )
            )
            Task.detached(priority: .utility) {
                try? await JarvisOperationalEvidenceRecorder.shared.recordScreenCapture(
                    milliseconds: captureMilliseconds,
                    payloadBytes: image.data.count
                )
            }
        } catch ScreenCaptureServiceError.tccPermissionDenied(let permission) {
            screenCaptureAuthorized = ScreenCaptureService.isAuthorized
            let jobID = activeJobID
            let acknowledged = await Task.detached(priority: .userInitiated) {
                Self.reportTCCPermissionDenied(
                    permission: permission,
                    operation: .screenTurn,
                    jobID: jobID,
                    secret: secret
                )
            }.value
            privacyLogger.error(
                "tcc_permission_denied permission=\(permission.rawValue, privacy: .public) ipc_acknowledged=\(acknowledged)"
            )
            announceVoiceFailure(
                String(localized: "El permiso de privacidad necesario fue revocado. No reintentaré esta acción.", bundle: .module),
                code: "tcc_permission_denied"
            )
            return
        } catch {
            logger.error("screen_turn_failed stage=capture")
            announceVoiceFailure(
                String(localized: "No pude capturar la pantalla.", bundle: .module),
                code: "screen_capture_failed"
            )
            return
        }

        await startVisualVoiceTurn(image, secret: secret)
    }

    private func startVisualVoiceTurn(
        _ image: LocalImageAttachment,
        secret: Data
    ) async {
        logger.info("visual_voice_turn_started")

        let capture = await captureSpokenPrompt()
        guard userSessionAvailable else {
            voiceState = .idle
            return
        }
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture {
                logger.error(
                    "visual_turn_failed stage=voice reason=\(reason.rawValue, privacy: .public)"
                )
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenCaptureFailure(for: reason.rawValue),
                    code: reason.rawValue
                )
            }
            return
        }
        lastSpeakerID = transcript.speakerID

        voiceState = .submitting
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit
        else {
            voiceState = .idle
            return
        }
        let conversationDecision = voiceConversationDecision(for: trustedTranscript)
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .image(image, transcript: trustedTranscript),
                conversationID: conversationDecision.conversationID,
                persistConversation: conversationDecision.persistAcceptedConversation,
                secret: secret
            )
        }.value
        guard userSessionExecutionGate.isCurrent(sessionPermit) else {
            if let submission {
                _ = await Task.detached(priority: .userInitiated) {
                    Self.cancelJob(submission.jobID, secret: secret)
                }.value
            }
            voiceState = .idle
            return
        }
        guard let submission else {
            logger.error("visual_turn_failed stage=submit")
            announceVoiceFailure(
                String(localized: "No pude comunicarme con el servicio local.", bundle: .module),
                code: "daemon_unavailable"
            )
            return
        }
        logger.info("visual_voice_turn_submitted")
        await trackSubmission(
            submission,
            conversationDecision: conversationDecision,
            secret: secret
        )
    }

    func submitVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        secret: Data
    ) async {
        if requiresBrowserResolution(transcript.text) {
            guard let resolution = await resolveBrowserTarget(for: transcript, secret: secret) else {
                logger.error("browser_discovery_failed")
                announceVoiceFailure(
                    String(localized: "No pude verificar qué navegadores están disponibles; no ejecuté la búsqueda.", bundle: .module),
                    code: "browser_discovery_failed"
                )
                return
            }
            if resolution.requiresSelection {
                presentBrowserSelection(
                    transcript: transcript,
                    options: resolution.browsers
                )
                return
            }
            await submitResolvedVoiceTranscript(
                transcript,
                preferredBrowserBundleIdentifier: resolution.selectedBundleIdentifier,
                secret: secret
            )
            return
        }
        await submitResolvedVoiceTranscript(
            transcript,
            preferredBrowserBundleIdentifier: nil,
            secret: secret
        )
    }

    func submitResolvedVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        preferredBrowserBundleIdentifier: String?,
        secret: Data
    ) async {
        voiceState = .submitting
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit
        else {
            voiceState = .idle
            return
        }
        let conversationDecision = voiceConversationDecision(for: trustedTranscript)
        let activeApplicationBundleIdentifier = NSWorkspace.shared.frontmostApplication?
            .bundleIdentifier
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .voice(
                    trustedTranscript,
                    preferredBrowserBundleIdentifier: preferredBrowserBundleIdentifier,
                    activeApplicationBundleIdentifier: activeApplicationBundleIdentifier
                ),
                conversationID: conversationDecision.conversationID,
                persistConversation: conversationDecision.persistAcceptedConversation,
                secret: secret
            )
        }.value
        guard userSessionExecutionGate.isCurrent(sessionPermit) else {
            if let submission {
                _ = await Task.detached(priority: .userInitiated) {
                    Self.cancelJob(submission.jobID, secret: secret)
                }.value
            }
            voiceState = .idle
            return
        }
        guard let submission else {
            logger.error("voice_turn_failed stage=submit")
            announceVoiceFailure(
                String(localized: "No pude comunicarme con el servicio local.", bundle: .module),
                code: "daemon_unavailable"
            )
            return
        }
        logger.info("voice_turn_submitted")
        await trackSubmission(
            submission,
            conversationDecision: conversationDecision,
            secret: secret
        )
    }
}
