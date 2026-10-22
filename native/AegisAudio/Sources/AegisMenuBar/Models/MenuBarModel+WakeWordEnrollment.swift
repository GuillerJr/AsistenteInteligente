import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for WakeWordEnrollment; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func inspectWakeWordCapability() async {
        wakeWordCapability = await Task.detached(priority: .utility) {
            WakeWordCapability.inspect()
        }.value
        persistReadinessSnapshot()
    }

    func initializeWakeWordListening() async {
        let capabilities = await Task.detached(priority: .utility) {
            (
                WakeWordCapability.inspect(),
                SpeakerIdentityCapability.snapshot(),
                ComputerControlService.inspect()
            )
        }.value
        wakeWordCapability = capabilities.0
        applySpeakerIdentitySnapshot(capabilities.1)
        computerControlCapability = capabilities.2
        computerControlLogger.info(
            "capability_initialized state=\(String(describing: capabilities.2), privacy: .public)"
        )
        logPrivacyCapabilities(event: "initialized")
        persistReadinessSnapshot()
        guard
            wakeWordCapability == .ready,
            speakerIdentityCapability == .ready,
            effectiveSpeakerOwnerIdentifier != nil,
            speakerIdentityModelFingerprint != nil
        else {
            wakeWordDetector.stop()
            wakeWordListeningState = .unavailable
            wakeWordPauseReason = nil
            return
        }
        if wakeWordOptedIn {
            await startWakeWordListening()
        } else {
            wakeWordListeningState = .off
            wakeWordPauseReason = nil
        }
    }

    func setWakeWordListeningEnabled(_ enabled: Bool) async {
        defer { persistReadinessSnapshot() }
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordOptedIn = enabled
        UserDefaults.standard.set(enabled, forKey: wakeWordOptInDefaultsKey)
        guard enabled else {
            wakeWordDetector.stop()
            wakeWordListeningState = .off
            wakeWordPauseReason = nil
            wakeWordLogger.info("wake_word_disabled")
            return
        }
        await inspectWakeWordCapability()
        await refreshSpeakerIdentityConfiguration()
        guard
            wakeWordCapability == .ready,
            speakerIdentityCapability == .ready,
            effectiveSpeakerOwnerIdentifier != nil,
            speakerIdentityModelFingerprint != nil
        else {
            wakeWordListeningState = .unavailable
            wakeWordPauseReason = nil
            return
        }
        await startWakeWordListening()
    }

    func refreshWakeWordEnrollment() async {
        guard !wakeWordEnrollmentState.isBusy else {
            return
        }
        wakeWordEnrollmentState = .loading
        let progress = await Task.detached(priority: .utility) {
            try? WakeWordEnrollmentRecorder().progress()
        }.value
        guard let progress else {
            wakeWordEnrollmentState = .failed(.unsafeStorage)
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = progress.isReady ? .ready : .idle
    }

    func recordWakeWordSample(_ label: WakeWordEnrollmentLabel) async {
        refreshPermissions()
        guard canRecordWakeWordSample else {
            return
        }
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        wakeWordEnrollmentState = .arming(label)
        do {
            try await Task.sleep(for: .seconds(1))
        } catch {
            wakeWordEnrollmentState = .idle
            return
        }
        wakeWordEnrollmentState = .recording(label)
        let outcome = await Task.detached(priority: .userInitiated) {
            do {
                return EnrollmentOutcome.success(
                    try WakeWordEnrollmentRecorder().record(label: label)
                )
            } catch let error as WakeWordEnrollmentError {
                return EnrollmentOutcome.failure(error)
            } catch {
                return EnrollmentOutcome.failure(.recordingFailed)
            }
        }.value
        guard case let .success(progress) = outcome else {
            if case let .failure(error) = outcome {
                wakeWordEnrollmentState = .failed(error)
            }
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = progress.isReady ? .ready : .idle
    }

    func clearWakeWordSamples() async {
        guard !wakeWordEnrollmentState.isBusy else {
            return
        }
        wakeWordEnrollmentState = .clearing
        let outcome = await Task.detached(priority: .utility) {
            do {
                return EnrollmentOutcome.success(
                    try WakeWordEnrollmentRecorder().clearSamples()
                )
            } catch let error as WakeWordEnrollmentError {
                return EnrollmentOutcome.failure(error)
            } catch {
                return EnrollmentOutcome.failure(.unsafeStorage)
            }
        }.value
        guard case let .success(progress) = outcome else {
            if case let .failure(error) = outcome {
                wakeWordEnrollmentState = .failed(error)
            }
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = .idle
    }
}
