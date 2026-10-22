import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Enrollment; observable storage remains in MenuBarModel.
extension MenuBarModel {
    var canRecordWakeWordSample: Bool {
        microphonePermission == .authorized
            && !voiceState.isBusy
            && !wakeWordEnrollmentState.isBusy
            && !speakerEnrollmentState.isBusy
            && !speakerModelTrainingState.isBusy
    }

    var canModifySpeakerEnrollment: Bool {
        !voiceState.isBusy
            && !wakeWordEnrollmentState.isBusy
            && !speakerEnrollmentState.isBusy
            && !speakerModelTrainingState.isBusy
    }

    var canRecordSpeakerSample: Bool {
        microphonePermission == .authorized && canModifySpeakerEnrollment
    }

    var effectiveSpeakerOwnerIdentifier: String? {
        return SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: speakerIdentityIdentifiers,
            selectedIdentifier: selectedSpeakerOwnerIdentifier,
            selectedModelFingerprint: selectedSpeakerOwnerModelFingerprint,
            activeModelFingerprint: speakerIdentityModelFingerprint
        )
    }

    var speakerOwnerSelectionNeedsReconfirmation: Bool {
        guard
            let selectedSpeakerOwnerIdentifier,
            speakerIdentityIdentifiers.contains(selectedSpeakerOwnerIdentifier)
        else {
            return false
        }
        return selectedSpeakerOwnerModelFingerprint != speakerIdentityModelFingerprint
    }

    func refreshSpeakerEnrollment() async {
        guard !speakerEnrollmentState.isBusy, !speakerModelTrainingState.isBusy else {
            return
        }
        speakerEnrollmentState = .loading
        let outcome = await Task.detached(priority: .utility) {
            do {
                return SpeakerEnrollmentOutcome.success(
                    try SpeakerEnrollmentRecorder().progress()
                )
            } catch let error as SpeakerEnrollmentError {
                return SpeakerEnrollmentOutcome.failure(error)
            } catch {
                return SpeakerEnrollmentOutcome.failure(.unsafeStorage)
            }
        }.value
        applySpeakerEnrollmentOutcome(outcome)
    }

    func refreshSpeakerIdentityConfiguration() async {
        let snapshot = await Task.detached(priority: .utility) {
            SpeakerIdentityCapability.snapshot()
        }.value
        applySpeakerIdentitySnapshot(snapshot)
    }

    func trainSpeakerIdentityModel() async {
        guard
            speakerEnrollmentProgress.isReady,
            speakerIdentityCapability != .ready,
            !speakerModelTrainingState.isBusy,
            canModifySpeakerEnrollment
        else {
            return
        }
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        speakerModelTrainingState = .training
        let outcome = await Task.detached(priority: .utility) {
            do {
                try SpeakerModelTrainer().train()
                return SpeakerModelTrainingOutcome.success
            } catch let error as SpeakerModelTrainingError {
                return SpeakerModelTrainingOutcome.failure(error)
            } catch {
                return SpeakerModelTrainingOutcome.failure(.trainingFailed)
            }
        }.value
        switch outcome {
        case .success:
            let snapshot = await Task.detached(priority: .utility) {
                SpeakerIdentityCapability.snapshot()
            }.value
            applySpeakerIdentitySnapshot(snapshot)
            speakerModelTrainingState = speakerIdentityCapability == .ready
                ? .ready
                : .failed(.invalidModel)
        case let .failure(error):
            speakerModelTrainingState = .failed(error)
        }
    }

    func addSpeakerProfile(_ identifier: String) async {
        await mutateSpeakerEnrollment(.add(identifier))
    }

    func removeSpeakerProfile(_ identifier: String) async {
        await mutateSpeakerEnrollment(.remove(identifier))
    }

    func clearSpeakerSamples() async {
        await mutateSpeakerEnrollment(.clearSamples)
    }

    func setSpeakerOwnerIdentifier(_ identifier: String?) {
        guard canModifySpeakerEnrollment else { return }
        if let identifier {
            guard
                speakerIdentityIdentifiers.contains(identifier),
                let speakerIdentityModelFingerprint
            else {
                return
            }
            guard
                selectedSpeakerOwnerIdentifier != identifier
                    || selectedSpeakerOwnerModelFingerprint != speakerIdentityModelFingerprint
            else {
                return
            }
            selectedSpeakerOwnerIdentifier = identifier
            selectedSpeakerOwnerModelFingerprint = speakerIdentityModelFingerprint
            UserDefaults.standard.set(identifier, forKey: speakerOwnerDefaultsKey)
            UserDefaults.standard.set(
                speakerIdentityModelFingerprint,
                forKey: speakerOwnerModelDefaultsKey
            )
        } else {
            guard
                selectedSpeakerOwnerIdentifier != nil
                    || selectedSpeakerOwnerModelFingerprint != nil
            else {
                return
            }
            selectedSpeakerOwnerIdentifier = nil
            selectedSpeakerOwnerModelFingerprint = nil
            UserDefaults.standard.removeObject(forKey: speakerOwnerDefaultsKey)
            UserDefaults.standard.removeObject(forKey: speakerOwnerModelDefaultsKey)
        }
        clearVoiceConversationSession()
        let shouldRestartWakeWord = wakeWordDetector.isRunning
        if shouldRestartWakeWord {
            wakeWordDetector.stop()
            wakeWordListeningState = .paused
            wakeWordPauseReason = .resuming
            scheduleWakeWordResume(if: effectiveSpeakerOwnerIdentifier != nil)
        }
        logger.info(
            "speaker_owner_selection_changed configured=\(identifier != nil, privacy: .public)"
        )
    }

    private func mutateSpeakerEnrollment(_ mutation: SpeakerEnrollmentMutation) async {
        guard canModifySpeakerEnrollment else { return }
        speakerEnrollmentState = .updating
        let outcome = await Task.detached(priority: .utility) {
            do {
                let recorder = SpeakerEnrollmentRecorder()
                let progress = switch mutation {
                case let .add(identifier):
                    try recorder.addProfile(identifier)
                case let .remove(identifier):
                    try recorder.removeProfile(identifier)
                case .clearSamples:
                    try recorder.clearSamples()
                }
                return SpeakerEnrollmentOutcome.success(
                    progress
                )
            } catch let error as SpeakerEnrollmentError {
                return SpeakerEnrollmentOutcome.failure(error)
            } catch {
                return SpeakerEnrollmentOutcome.failure(.unsafeStorage)
            }
        }.value
        applySpeakerEnrollmentOutcome(outcome)
    }

    func recordSpeakerSample(_ target: SpeakerEnrollmentTarget) async {
        refreshPermissions()
        guard canRecordSpeakerSample else { return }
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        speakerEnrollmentState = .arming(target)
        do {
            try await Task.sleep(for: .seconds(1))
        } catch {
            speakerEnrollmentState = .idle
            return
        }
        speakerEnrollmentState = .recording(target)
        let outcome = await Task.detached(priority: .userInitiated) {
            do {
                return SpeakerEnrollmentOutcome.success(
                    try SpeakerEnrollmentRecorder().record(target: target)
                )
            } catch let error as SpeakerEnrollmentError {
                return SpeakerEnrollmentOutcome.failure(error)
            } catch {
                return SpeakerEnrollmentOutcome.failure(.recordingFailed)
            }
        }.value
        applySpeakerEnrollmentOutcome(outcome)
    }

    private func applySpeakerEnrollmentOutcome(_ outcome: SpeakerEnrollmentOutcome) {
        switch outcome {
        case let .success(progress):
            speakerEnrollmentProgress = progress
            speakerEnrollmentState = progress.isReady ? .ready : .idle
        case let .failure(error):
            speakerEnrollmentState = .failed(error)
        }
    }

    func applySpeakerIdentitySnapshot(_ snapshot: SpeakerIdentityCapabilitySnapshot) {
        speakerIdentityCapability = snapshot.state
        speakerIdentityIdentifiers = snapshot.speakerIdentifiers
        speakerIdentityModelFingerprint = snapshot.modelFingerprint
        if
            snapshot.state == .ready,
            conversationSpeakerID != nil,
            conversationModelFingerprint != snapshot.modelFingerprint
        {
            clearVoiceConversationSession()
            logger.info("voice_conversation_reset source=speaker_model_changed")
        }
        if snapshot.state == .ready {
            speakerModelTrainingState = .ready
        } else if !speakerModelTrainingState.isBusy {
            speakerModelTrainingState = .idle
        }
        persistReadinessSnapshot()
    }

    enum EnrollmentOutcome: Sendable {
        case success(WakeWordEnrollmentProgress)
        case failure(WakeWordEnrollmentError)
    }

    private enum SpeakerEnrollmentOutcome: Sendable {
        case success(SpeakerEnrollmentProgress)
        case failure(SpeakerEnrollmentError)
    }

    private enum SpeakerModelTrainingOutcome: Sendable {
        case success
        case failure(SpeakerModelTrainingError)
    }

    private enum SpeakerEnrollmentMutation: Sendable {
        case add(String)
        case remove(String)
        case clearSamples
    }
}
