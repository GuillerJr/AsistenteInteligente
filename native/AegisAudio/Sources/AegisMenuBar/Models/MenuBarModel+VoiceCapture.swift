import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for VoiceCapture; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func captureSpokenPrompt(
        playCue: Bool = true,
        durationSeconds: TimeInterval = VoiceCapturePolicy.normalTurnMaximumSeconds
    ) async -> CaptureOutcome {
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        voiceState = .listening
        mascotUsesVoice = true
        voiceInputText = nil
        voiceOutputText = nil
        mascotNotice = nil
        chat.error = nil
        let activityHandler: @Sendable (Float) -> Void = { [weak self] level in
            Task { @MainActor [weak self] in
                self?.voiceActivityLevel = level
            }
        }
        let selectedOwnerIdentifier = effectiveSpeakerOwnerIdentifier
        pendingOwnerVoiceVariant = nil
        let speakerModelFingerprint = speakerIdentityModelFingerprint
        let transcriber = LocalSpeechTranscriber(
            writer: NDJSONWriter(handle: .nullDevice),
            activityHandler: activityHandler,
            captureReadyHandler: {
                guard playCue else { return }
                Task { @MainActor in NSSound.beep() }
            },
            selectedOwnerIdentifier: selectedOwnerIdentifier,
            expectedSpeakerModelFingerprint: speakerModelFingerprint
        )
        activeTranscriber = transcriber
        if pushToTalkHeld { transcriber.setHeld(true) }
        defer {
            if activeTranscriber === transcriber {
                activeTranscriber = nil
            }
        }
        let capture = await Task.detached(priority: .userInitiated) {
            Self.captureTranscript(
                with: transcriber,
                durationSeconds: durationSeconds
            )
        }.value
        pendingOwnerVoiceVariant = transcriber.capturedOwnerVoiceVariant
        voiceActivityLevel = 0
        if mascotCancellingCapture, activeTranscriber === transcriber {
            mascotCancellingCapture = false
            pendingOwnerVoiceVariant = nil
            voiceState = .idle
            scheduleWakeWordResume(if: wakeWordOptedIn)
            return .cancelled
        }
        if userSessionAvailable, case let .transcript(transcript) = capture {
            voiceInputText = String(transcript.text.prefix(4_096))
        }
        return capture
    }

    nonisolated private static func captureTranscript(
        with transcriber: LocalSpeechTranscriber,
        durationSeconds: TimeInterval
    ) -> CaptureOutcome {
        do {
            guard let transcript = try transcriber.runForFinalTranscript(
                durationSeconds: VoiceCapturePolicy.boundedNormalTurnDuration(durationSeconds),
                intervalMilliseconds: 50,
                localeIdentifier: "es-US"
            ) else {
                return .failed(.noFinalTranscript)
            }
            return .transcript(transcript)
        } catch let error as LocalSpeechTranscriberError {
            return .failed(CaptureFailure(error))
        } catch {
            return .failed(.unexpected)
        }
    }

    enum CaptureOutcome: Sendable {
        case transcript(SpeechTranscriptEvent)
        case failed(CaptureFailure)
        case cancelled
    }

    enum CaptureFailure: String, Equatable, Sendable {
        case invalidConfiguration
        case microphonePermission
        case speechPermission
        case unsupportedLocale
        case recognizerUnavailable
        case onDeviceRecognitionUnavailable
        case invalidInputFormat
        case noAudibleInput
        case voiceActivityUnavailable
        case speakerIdentityUnverified
        case recognitionFailed
        case cancelled
        case noFinalTranscript
        case unexpected

        init(_ error: LocalSpeechTranscriberError) {
            switch error {
            case .invalidConfiguration:
                self = .invalidConfiguration
            case .microphonePermissionRequired:
                self = .microphonePermission
            case .speechPermissionRequired:
                self = .speechPermission
            case .unsupportedLocale:
                self = .unsupportedLocale
            case .recognizerUnavailable:
                self = .recognizerUnavailable
            case .onDeviceRecognitionUnavailable:
                self = .onDeviceRecognitionUnavailable
            case .invalidInputFormat:
                self = .invalidInputFormat
            case .noAudibleInput:
                self = .noAudibleInput
            case .voiceActivityUnavailable:
                self = .voiceActivityUnavailable
            case .speakerIdentityUnverified:
                self = .speakerIdentityUnverified
            case .recognitionFailed:
                self = .recognitionFailed
            case .cancelled:
                self = .cancelled
            }
        }
    }
}
