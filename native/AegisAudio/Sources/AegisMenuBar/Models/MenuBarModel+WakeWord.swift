import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for WakeWord; observable storage remains in MenuBarModel.
extension MenuBarModel {
    var wakeWordRuntimeAvailable: Bool {
        daemonState == .online
            && securityState == .intact
            && hybridBrainReady
    }

    private var wakeWordMayResume: Bool {
        userSessionAvailable
            && wakeWordOptedIn
            && wakeWordCapability == .ready
            && speakerIdentityCapability == .ready
            && effectiveSpeakerOwnerIdentifier != nil
            && speakerIdentityModelFingerprint != nil
            && microphonePermission == .authorized
            && wakeWordRuntimeAvailable
            && wakeWordThermalAvailable
            && wakeWordEnergyAvailable
            && (wakeWordListeningState == .paused
                || wakeWordListeningState == .unavailable)
    }

    func startWakeWordListening() async {
        refreshPermissions()
        if wakeWordDetector.isRunning {
            wakeWordListeningState = .listening
            wakeWordPauseReason = nil
            return
        }
        guard
            userSessionAvailable,
            wakeWordOptedIn,
            wakeWordCapability == .ready,
            speakerIdentityCapability == .ready,
            microphonePermission == .authorized,
            !wakeWordEnrollmentState.isBusy,
            let ownerIdentifier = effectiveSpeakerOwnerIdentifier,
            let modelFingerprint = speakerIdentityModelFingerprint
        else {
            if !userSessionAvailable, wakeWordCapability == .ready {
                wakeWordListeningState = .paused
                wakeWordPauseReason = .session
            } else {
                wakeWordListeningState = microphonePermission == .authorized
                    ? .failed
                    : .unavailable
                wakeWordPauseReason = nil
            }
            return
        }
        guard wakeWordThermalAvailable else {
            wakeWordListeningState = .paused
            wakeWordPauseReason = .thermal
            return
        }
        guard wakeWordEnergyAvailable else {
            wakeWordListeningState = .paused
            wakeWordPauseReason = .lowPower
            return
        }
        wakeWordListeningState = .starting
        wakeWordPauseReason = nil
        let detector = wakeWordDetector
        let detectionHandler: @Sendable () -> Void = { [weak self] in
            Task { @MainActor [weak self] in
                await self?.handleWakeWordDetection()
            }
        }
        let failureHandler: @Sendable () -> Void = { [weak self] in
            Task { @MainActor [weak self] in
                self?.wakeWordLogger.error("wake_word_failed stage=analysis")
                self?.handleWakeWordFailure()
            }
        }
        let acousticAuditSecret = ipcSecret
        let activityHandler: @Sendable (AcousticActivityEvent) -> Void = { [weak self] event in
            Task { @MainActor [weak self] in
                self?.handleAcousticActivity(event)
            }
            guard let secret = acousticAuditSecret else { return }
            Task.detached(priority: .utility) {
                _ = Self.recordAcousticAuditEvent(
                    "noise_floor_transition",
                    data: [
                        "rms_microunits": Int((event.rms * 1_000_000).rounded()),
                        "noise_floor_microunits": Int(
                            (event.noiseFloor * 1_000_000).rounded()
                        ),
                        "threshold_microunits": Int(
                            (event.activeThreshold * 1_000_000).rounded()
                        ),
                        "voice_active": event.voiceActive,
                    ],
                    secret: secret
                )
            }
        }
        let started = await Task.detached(priority: .utility) {
            do {
                try detector.start(
                    selectedOwnerIdentifier: ownerIdentifier,
                    expectedSpeakerModelFingerprint: modelFingerprint,
                    detectionHandler: detectionHandler,
                    failureHandler: failureHandler,
                    activityHandler: activityHandler
                )
                return true
            } catch {
                return false
            }
        }.value
        wakeWordListeningState = started ? .listening : .failed
        wakeWordPauseReason = nil
        if started {
            wakeWordLogger.info("wake_word_enabled")
            scheduleWakeWordStabilityReset()
        } else {
            wakeWordLogger.error("wake_word_failed stage=start")
            handleWakeWordFailure()
        }
    }

    private func handleWakeWordDetection() async {
        guard wakeWordListeningState == .listening else { return }
        try? await JarvisOperationalEvidenceRecorder.shared.recordWakeWordDetection()
        wakeWordLogger.info("wake_word_detected")
        if voiceState == .speaking || voiceState == .processing || voiceState == .submitting {
            await interruptCurrentTurnAndListen(source: .wakeWord)
            return
        }
        if pendingBrowserSelection != nil {
            await performVoiceTurn(playCue: false)
            return
        }
        guard canStartVoiceTurn, voiceState != .awaitingAuthorization else { return }
        let shouldResume = pauseWakeWordListening()
        await startVoiceTurn()
        scheduleWakeWordResume(if: shouldResume)
    }

    func interruptCurrentTurnAndListen(source: VoiceInterruptionSource) async {
        guard !chat.isBusy else { return }
        wakeWordLogger.info(
            "voice_turn_interrupted source=\(source.rawValue, privacy: .public)"
        )
        cancelFollowUpWindow()
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .audio
        let interruptedJobID = activeJobID
        activeJobID = nil
        activeComputerUseJobID = nil
        cancelApprovalExpiry()
        cancelDualAuthorization()
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        speechStreamChunker.reset()
        speechStreamOpen = false
        guard let secret = ipcSecret else {
            isInterruptingSpeech = speechOutput.hasAudiblePlayback
            await speechOutput.interruptWithFade()
            isInterruptingSpeech = false
            wakeWordLogger.error("voice_turn_interruption_failed stage=ipc_auth")
            voiceState = .failed
            return
        }

        let cancellationTask = Task.detached(priority: .userInitiated) {
            guard let jobID = interruptedJobID else { return true }
            return Self.cancelJob(jobID, secret: secret)
        }
        let fadesAudio = speechOutput.hasAudiblePlayback
        isInterruptingSpeech = fadesAudio
        await speechOutput.interruptWithFade()
        isInterruptingSpeech = false
        let jobCancellationSucceeded = await cancellationTask.value
        let auditSucceeded = await Task.detached(priority: .utility) {
            Self.recordAcousticAuditEvent(
                "voice_interruption",
                data: [
                    "job_present": interruptedJobID != nil,
                    "job_cancelled": jobCancellationSucceeded,
                    "source": source.rawValue,
                ],
                secret: secret
            )
        }.value
        guard jobCancellationSucceeded, auditSucceeded else {
            let stage = jobCancellationSucceeded ? "audit" : "job_cancel"
            wakeWordLogger.error("voice_turn_interruption_failed stage=\(stage, privacy: .public)")
            voiceState = .failed
            return
        }
        try? await JarvisOperationalEvidenceRecorder.shared.recordSuccessfulInterruption()
        voiceState = .idle
        await performVoiceTurn(playCue: false)
    }

    func suspendWakeWordForSystemSleep() {
        voiceReplayBuffer.clear()
        speechOutput.stop()
        speechOutput.clearReplay()
        voiceOutputText = nil
        cancelFollowUpWindow()
        guard wakeWordOptedIn else { return }
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordDetector.stop()
        wakeWordListeningState = wakeWordCapability == .ready ? .paused : .unavailable
        wakeWordPauseReason = wakeWordCapability == .ready ? .system : nil
        wakeWordLogger.info("wake_word_paused reason=system_sleep")
    }

    func suspendForUserSessionLock() async {
        guard userSessionAvailable else { return }
        userSessionAvailable = false
        voiceInputText = nil
        mascotNoticeTask?.cancel()
        mascotNoticeTask = nil
        mascotNotice = nil
        if let jobID = chat.transcript.jobID {
            chat.transcript.finish(String(localized: "Solicitud detenida al bloquear la sesión.", bundle: .module), jobID: jobID, interrupted: true)
        }
        pendingInput = nil
        pendingRecovery = nil
        pendingBrowserSelection = nil
        browserChoiceTask?.cancel()
        browserChoiceTask = nil
        voiceReplayBuffer.clear()
        speechOutput.clearReplay()
        voiceOutputText = nil
        cancelFollowUpWindow()
        userSessionExecutionGate.suspend()
        ownerPresenceLease.revoke()
        activeTranscriber?.cancel()
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordDetector.stop()
        wakeWordListeningState = wakeWordCapability == .ready ? .paused : .unavailable
        wakeWordPauseReason = wakeWordCapability == .ready ? .session : nil
        speechOutput.stop()
        speechStreamChunker.reset()
        speechStreamOpen = false
        if let jobID = activeJobID, let secret = ipcSecret {
            _ = await Task.detached(priority: .userInitiated) {
                Self.cancelJob(jobID, secret: secret)
            }.value
        }
        activeJobID = nil
        activeComputerUseJobID = nil
        cancelApprovalExpiry()
        cancelDualAuthorization()
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        voiceState = .idle
        wakeWordLogger.info("wake_word_paused reason=session_locked")
    }

    func resumeAfterUserSessionUnlock() {
        guard !userSessionAvailable else { return }
        userSessionExecutionGate.resume()
        userSessionAvailable = true
        _ = ownerPresenceLease.authorize(at: ProcessInfo.processInfo.systemUptime)
        guard wakeWordMayResume else { return }
        wakeWordListeningState = .paused
        wakeWordPauseReason = .resuming
        wakeWordLogger.info("wake_word_resume_scheduled reason=session_unlocked")
        scheduleWakeWordResume(if: true)
    }

    func resumeWakeWordAfterSystemWake() {
        wakeWordThermalAvailable = WakeWordThermalPolicy.allowsListening(
            ProcessInfo.processInfo.thermalState
        )
        wakeWordThermalStateLabel = AcousticThermalEvent(
            state: ProcessInfo.processInfo.thermalState
        ).label
        wakeWordEnergyAvailable = WakeWordEnergyPolicy.allowsListening(
            lowPowerModeEnabled: ProcessInfo.processInfo.isLowPowerModeEnabled
        )
        guard wakeWordMayResume else { return }
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .resuming
        wakeWordLogger.info("wake_word_resume_scheduled reason=system_wake")
        scheduleWakeWordResume(if: true)
    }

    func reconcileWakeWordPermission(from previous: MicrophonePermission) {
        reconcileWakeWordAvailability(
            previousAvailable: previous == .authorized,
            currentAvailable: microphonePermission == .authorized,
            stoppedState: .unavailable,
            stoppedReason: nil,
            resumeReason: "microphone_authorized",
            pauseReason: "microphone_unavailable"
        )
    }

    func reconcileWakeWordRuntime(from previousAvailable: Bool) {
        reconcileWakeWordAvailability(
            previousAvailable: previousAvailable,
            currentAvailable: wakeWordRuntimeAvailable,
            stoppedState: .paused,
            stoppedReason: .runtime,
            resumeReason: "runtime_available",
            pauseReason: "runtime_unavailable"
        )
    }

    func reconcileWakeWordThermalState(_ event: AcousticThermalEvent) {
        let previousAvailable = wakeWordThermalAvailable
        let previousLabel = wakeWordThermalStateLabel
        wakeWordThermalAvailable = event.allowsListening
        wakeWordThermalStateLabel = event.label
        if previousLabel != event.label, let secret = ipcSecret {
            let eventType = event.allowsListening ? "thermal_resume" : "thermal_pause"
            Task.detached(priority: .utility) {
                _ = Self.recordAcousticAuditEvent(
                    eventType,
                    data: ["state": event.label],
                    secret: secret
                )
            }
        }
        reconcileWakeWordAvailability(
            previousAvailable: previousAvailable,
            currentAvailable: wakeWordThermalAvailable,
            stoppedState: .paused,
            stoppedReason: .thermal,
            resumeReason: "thermal_available",
            pauseReason: "thermal_pressure"
        )
    }

    func reconcileWakeWordEnergyState() {
        let previousAvailable = wakeWordEnergyAvailable
        wakeWordEnergyAvailable = WakeWordEnergyPolicy.allowsListening(
            lowPowerModeEnabled: ProcessInfo.processInfo.isLowPowerModeEnabled
        )
        reconcileWakeWordAvailability(
            previousAvailable: previousAvailable,
            currentAvailable: wakeWordEnergyAvailable,
            stoppedState: .paused,
            stoppedReason: .lowPower,
            resumeReason: "low_power_disabled",
            pauseReason: "low_power_enabled"
        )
    }

    func synchronizeDaemonRuntimeState(
        trigger: AudioRuntimeTransitionCause,
        snapshot: ThermalBudgetSnapshot? = nil
    ) {
        let current = snapshot ?? ThermalMonitor.currentSnapshot()
        guard
            daemonState == .online,
            securityState == .intact,
            let ipcSecret,
            runtimeStateSequence < 9_007_199_254_740_991
        else {
            return
        }
        let thermalState = current.thermalState
        let lowPowerMode = current.lowPowerMode
        let effectiveCause: AudioRuntimeTransitionCause
        if !thermalState.allowsFullRuntime {
            effectiveCause = .thermalThrottle
        } else if lowPowerMode {
            effectiveCause = .lowPowerMode
        } else if trigger == .lowPowerDisabled {
            effectiveCause = .lowPowerDisabled
        } else {
            effectiveCause = .thermalRecovery
        }
        runtimeStateSequence += 1
        let sequence = runtimeStateSequence
        let sourceID = runtimeStateSourceID
        Task.detached(priority: .utility) {
            _ = try? LocalIPCClient(secret: ipcSecret).updateAudioRuntimeState(
                sourceID: sourceID,
                sequence: sequence,
                cause: effectiveCause,
                thermalState: thermalState,
                lowPowerMode: lowPowerMode,
                powerSource: current.powerSource
            )
        }
    }

    private func reconcileWakeWordAvailability(
        previousAvailable: Bool,
        currentAvailable: Bool,
        stoppedState: WakeWordListeningState,
        stoppedReason: WakeWordPauseReason?,
        resumeReason: String,
        pauseReason: String
    ) {
        switch WakeWordAvailabilityPolicy.action(
            previousAvailable: previousAvailable,
            currentAvailable: currentAvailable,
            active: wakeWordOptedIn && wakeWordCapability == .ready,
            startEligible: wakeWordMayResume,
            detectorRunning: wakeWordDetector.isRunning
        ) {
        case .none:
            return
        case .start:
            wakeWordLogger.info(
                "wake_word_resume_requested reason=\(resumeReason, privacy: .public)"
            )
            wakeWordListeningState = .paused
            wakeWordPauseReason = .resuming
            scheduleWakeWordResume(if: true)
        case .stop:
            wakeWordResumeTask?.cancel()
            wakeWordResumeTask = nil
            cancelWakeWordRecovery(resetGate: true)
            wakeWordDetector.stop()
            wakeWordListeningState = stoppedState
            wakeWordPauseReason = stoppedReason
            wakeWordLogger.info("wake_word_paused reason=\(pauseReason, privacy: .public)")
        }
    }

    func pauseWakeWordListening() -> Bool {
        guard wakeWordListeningState == .listening else {
            return false
        }
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = nil
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .audio
        return true
    }

    func scheduleWakeWordResume(if shouldResume: Bool) {
        guard shouldResume, wakeWordOptedIn else {
            return
        }
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = Task { @MainActor [weak self] in
            guard let self else { return }
            var gate = WakeWordResumeGate()
            while !Task.isCancelled {
                let audioIsBusy = voiceState.isBusy
                    || speechOutput.isActive
                    || wakeWordEnrollmentState.isBusy
                    || meetingRecording
                if gate.observe(
                    audioIsBusy: audioIsBusy,
                    at: ProcessInfo.processInfo.systemUptime
                ) {
                    break
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
            guard !Task.isCancelled, wakeWordOptedIn else { return }
            await startWakeWordListening()
        }
    }

    private func handleWakeWordFailure() {
        wakeWordDetector.stop()
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = nil
        guard wakeWordOptedIn else {
            wakeWordListeningState = .off
            wakeWordPauseReason = nil
            return
        }
        guard wakeWordRecoveryTask == nil else {
            return
        }
        guard wakeWordRecoveryGate.consumeRetry() else {
            wakeWordListeningState = .failed
            wakeWordPauseReason = nil
            return
        }
        wakeWordListeningState = .recovering
        wakeWordPauseReason = nil
        wakeWordLogger.info("wake_word_recovery_scheduled delay_seconds=2")
        wakeWordRecoveryTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(2))
            } catch {
                self?.wakeWordRecoveryTask = nil
                return
            }
            guard let self, wakeWordOptedIn else { return }
            wakeWordRecoveryTask = nil
            await startWakeWordListening()
        }
    }

    private func scheduleWakeWordStabilityReset() {
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(30))
            } catch {
                return
            }
            guard
                let self,
                wakeWordListeningState == .listening,
                wakeWordDetector.isRunning
            else {
                return
            }
            wakeWordRecoveryGate.reset()
            wakeWordStabilityTask = nil
        }
    }

    func cancelWakeWordRecovery(resetGate: Bool) {
        wakeWordRecoveryTask?.cancel()
        wakeWordRecoveryTask = nil
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = nil
        if resetGate {
            wakeWordRecoveryGate.reset()
        }
    }
}
