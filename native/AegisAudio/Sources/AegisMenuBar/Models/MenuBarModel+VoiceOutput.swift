import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for VoiceOutput; observable storage remains in MenuBarModel.
extension MenuBarModel {
    var voiceFailureTitle: String {
        LocalVoiceTurnFeedback.shortJobFailureTitle(for: lastVoiceFailureCode)
    }

    func preflightFailureMessage(
        screenCaptureRequired: Bool = false
    ) -> String {
        if voiceOutputState == .unconfigured {
            return LocalVoiceTurnFeedback.spokenJobFailure(for: "speech_provider_unconfigured")
                ?? voiceOutputState.title
        }
        return LocalVoiceTurnFeedback.spokenPreflightFailure(
            microphoneAuthorized: microphonePermission == .authorized,
            speechRecognitionAuthorized: speechPermission == .authorized,
            runtimeAvailable: daemonState == .online
                && securityState == .intact
                && hybridBrainReady
                && voiceOutputState == .ready
                && ipcSecret != nil,
            screenCaptureAuthorized: screenCaptureRequired ? screenCaptureAuthorized : nil
        )
    }

    func announceVoiceFailure(_ response: String?, code: String? = nil) {
        lastVoiceFailureCode = code
        if let response, let code, !["job_cancelled", "job_superseded"].contains(code) {
            pendingRecovery = PendingRecovery(code: code, message: response)
        }
        speechOutput.stop()
        voiceOutputText = response.map { String($0.prefix(2_000)) }
        if speechStreamOpen {
            speechStreamChunker.reset()
            speechStreamOpen = false
        }
        guard response != nil else {
            voiceState = .idle
            return
        }
        // Failure feedback remains local, immediate and silent; it is not a replay request.
        voiceState = .failed
        scheduleWakeWordResume(if: wakeWordOptedIn)
    }

    func speakCompletedResult(_ result: String) {
        lastVoiceFailureCode = nil
        let normalized = result.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        voiceOutputText = String(normalized.prefix(2_000))
        guard voiceOutputState == .ready else {
            preserveTextWithoutVoice(normalized)
            return
        }
        if speechStreamOpen {
            let chunks = speechStreamChunker.finish(normalized)
            guard !speechStreamChunker.isInvalid else {
                logger.error("voice_turn_failed stage=stream_integrity")
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenJobFailure(for: "job_stream_invalid"),
                    code: "job_stream_invalid"
                )
                return
            }
            voiceReplayBuffer.store(
                normalized,
                at: ProcessInfo.processInfo.systemUptime
            )
            for chunk in chunks {
                speechOutput.enqueue(chunk)
            }
            speechOutput.finishStream()
            return
        }
        let spokenText = String(normalized.prefix(2_000))
        guard !spokenText.isEmpty else {
            logger.error("voice_turn_failed stage=response")
            announceVoiceFailure(
                LocalVoiceTurnFeedback.spokenJobFailure(for: "empty_agent_response"),
                code: "empty_agent_response"
            )
            return
        }
        voiceReplayBuffer.store(
            spokenText,
            at: ProcessInfo.processInfo.systemUptime
        )
        voiceState = .speaking
        speakWithWakeWordIsolation(spokenText) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.completeConversationalPlayback(mode: "single_response")
        }
    }

    func speakWithWakeWordIsolation(
        _ text: String,
        replayOnly: Bool = false,
        completion: @escaping () -> Void
    ) {
        guard replayOnly || voiceOutputState == .ready else {
            preserveTextWithoutVoice(text)
            return
        }
        // Bind diagnostics to this playback; a later timer/replay must not inherit an old error.
        lastVoiceFailureCode = nil
        voiceOutputText = String(text.prefix(2_000))
        let isolatedCompletion: (SpeechPlaybackOutcome) -> Void = { [weak self] outcome in
            guard let self else { return }
            if outcome == .completed {
                completion()
            } else {
                self.lastVoiceFailureCode = outcome.failureCode
                self.voiceState = .failed
                self.logger.error("voice_playback_incomplete reason=\(outcome.failureCode ?? "unknown", privacy: .public)")
            }
            if !self.wakeWordDetector.isRunning {
                self.scheduleWakeWordResume(if: self.wakeWordOptedIn)
            }
        }
        if replayOnly {
            speechOutput.replayLocally(text, completion: isolatedCompletion)
        } else {
            speechOutput.speak(
                text,
                ipcSecret: ipcSecret,
                completion: isolatedCompletion
            )
        }
        if speechOutput.isActive {
            enableInterruptionListening()
        }
    }

    func enableInterruptionListening() {
        guard wakeWordOptedIn, wakeWordCapability == .ready else { return }
        Task { @MainActor [weak self] in
            guard let self, !wakeWordDetector.isRunning else { return }
            await startWakeWordListening()
        }
    }

    private func completeConversationalPlayback(mode: String) {
        speechStreamOpen = false
        Task.detached(priority: .utility) {
            try? await JarvisOperationalEvidenceRecorder.shared.recordCompletedPlayback()
        }
        logger.info("voice_turn_completed mode=\(mode, privacy: .public)")
        armFollowUpWindow()
    }

    private func armFollowUpWindow() {
        cancelFollowUpWindow()
        guard
            userSessionAvailable,
            pendingApproval == nil,
            pendingInput == nil,
            wakeWordOptedIn,
            wakeWordCapability == .ready,
            microphonePermission == .authorized,
            speechPermission == .authorized,
            wakeWordThermalAvailable,
            wakeWordEnergyAvailable
        else {
            voiceState = .completed
            return
        }

        followUpListeningGate.arm(
            at: ProcessInfo.processInfo.systemUptime,
            voiceIsActive: latestAcousticVoiceActive
        )
        voiceState = .followingUp
        voiceActivityLevel = latestAcousticVoiceActive ? 0.12 : 0.18
        logger.info("follow_up_window_armed duration_ms=5000")
        followUpWindowTask = Task { @MainActor [weak self] in
            guard let self else { return }
            if !wakeWordDetector.isRunning {
                await startWakeWordListening()
            }
            guard
                !Task.isCancelled,
                wakeWordDetector.isRunning,
                voiceState == .followingUp
            else {
                followUpListeningGate.cancel()
                if voiceState == .followingUp {
                    voiceState = .completed
                }
                followUpWindowTask = nil
                return
            }
            do {
                try await Task.sleep(for: .seconds(5))
            } catch {
                return
            }
            guard
                !Task.isCancelled,
                voiceState == .followingUp,
                followUpListeningGate.expire(
                    at: ProcessInfo.processInfo.systemUptime
                ) == .expire
            else { return }
            followUpWindowTask = nil
            voiceActivityLevel = 0
            voiceState = .idle
            logger.info("follow_up_window_expired mode=wake_word_only")
        }
    }

    func cancelFollowUpWindow() {
        followUpWindowTask?.cancel()
        followUpWindowTask = nil
        followUpListeningGate.cancel()
    }

    func handleAcousticActivity(_ event: AcousticActivityEvent) {
        latestAcousticVoiceActive = event.voiceActive
        guard voiceState == .followingUp else { return }
        voiceActivityLevel = event.voiceActive ? 0.72 : 0.18
        guard
            followUpListeningGate.observe(
                voiceIsActive: event.voiceActive,
                at: ProcessInfo.processInfo.systemUptime
            ) == .beginCapture
        else { return }
        followUpWindowTask?.cancel()
        followUpWindowTask = nil
        voiceState = .listening
        Task.detached(priority: .utility) {
            try? await JarvisOperationalEvidenceRecorder.shared.recordFollowUpVoiceTurn()
        }
        logger.info("follow_up_voice_detected source=adaptive_noise_floor")
        Task { @MainActor [weak self] in
            await self?.performVoiceTurn(
                playCue: false,
                admittedFromFollowUp: true
            )
        }
    }

    func consumeStreamSnapshot(_ text: String) -> Bool {
        let normalized = text.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        let chunks = speechStreamChunker.consume(normalized)
        guard !speechStreamChunker.isInvalid else { return false }
        voiceOutputText = String(normalized.prefix(2_000))
        guard voiceOutputState == .ready else {
            // Keep receiving the existing job's text; an absent voice is not a
            // reason to cancel an action or repeat it with a different provider.
            if speechStreamOpen { speechOutput.stop(); speechStreamOpen = false }
            return true
        }
        guard !chunks.isEmpty else { return true }
        if !speechStreamOpen {
            speechStreamOpen = true
            voiceState = .speaking
            speechOutput.beginStream(ipcSecret: ipcSecret) { [weak self] outcome in
                guard let self, voiceState == .speaking else { return }
                if outcome == .completed {
                    completeConversationalPlayback(mode: "streaming")
                } else {
                    lastVoiceFailureCode = outcome.failureCode
                    voiceState = .failed
                    scheduleWakeWordResume(if: wakeWordOptedIn)
                }
            }
        }
        for chunk in chunks {
            speechOutput.enqueue(chunk)
        }
        return true
    }

    private func preserveTextWithoutVoice(_ text: String) {
        speechOutput.stop()
        speechStreamOpen = false
        speechStreamChunker.reset()
        voiceOutputText = String(text.prefix(2_000))
        lastVoiceFailureCode = voiceOutputState == .unconfigured
            ? "speech_provider_unconfigured" : "speech_output_unavailable"
        voiceState = .failed
    }

    nonisolated static func recordAcousticAuditEvent(
        _ eventType: String,
        data: [String: Any],
        secret: Data
    ) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).recordSystemAuditEvent(
                eventType,
                component: "acoustic_sensor",
                data: data
            ),
            response.ok,
            response.payload["recorded"] as? Bool == true
        else {
            return false
        }
        return true
    }
}
