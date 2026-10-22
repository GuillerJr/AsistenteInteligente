import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for VoiceUtilities; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func handleLocalVoiceReplay(_ transcript: SpeechTranscriptEvent) async -> Bool {
        guard LocalVoiceReplayCommand.parse(transcript.text) != nil else { return false }
        guard
            let response = voiceReplayBuffer.response(
                at: ProcessInfo.processInfo.systemUptime
            )
        else {
            speakLocalVoiceReplay(
                LocalVoiceReplayCommand.unavailableSpokenResponse,
                event: "unavailable"
            )
            return true
        }
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            trustedTranscript.ownerPresenceVerified,
            trustedTranscript.ownerSpeakerProfile,
            userSessionAvailable
        else {
            logger.info("voice_replay_rejected reason=owner_unverified")
            speakLocalVoiceReplay(
                LocalVoiceReplayCommand.unverifiedSpokenResponse,
                event: "unverified"
            )
            return true
        }
        guard
            voiceReplayBuffer.response(at: ProcessInfo.processInfo.systemUptime)
                == response
        else {
            speakLocalVoiceReplay(
                LocalVoiceReplayCommand.unavailableSpokenResponse,
                event: "expired"
            )
            return true
        }
        logger.info("voice_replay_started")
        speakLocalVoiceReplay(response, event: "replayed")
        return true
    }

    private func speakLocalVoiceReplay(_ text: String, event: String) {
        guard event == "replayed" else {
            announceVoiceFailure(
                text,
                code: event == "unverified"
                    ? "owner_verification_required" : "speech_replay_unavailable"
            )
            return
        }
        voiceState = .speaking
        speakWithWakeWordIsolation(text, replayOnly: true) { [weak self] in
            guard let self, voiceState == .speaking else { return }
            voiceState = .completed
            logger.info("voice_replay_completed event=\(event, privacy: .public)")
        }
    }

    func handleLocalVoiceCapabilities(_ text: String) -> Bool {
        guard LocalVoiceCapabilitiesCommand.parse(text) != nil else { return false }
        let visualControlReady = computerControlCapability == .ready
        logger.info(
            "local_capabilities visual_control_ready=\(visualControlReady, privacy: .public)"
        )
        speakLocalVoiceUtility(
            LocalVoiceCapabilitiesCommand.spokenResponse(
                visualControlReady: visualControlReady
            ),
            utility: "capabilities",
            event: visualControlReady ? "full" : "visual_control_unavailable"
        )
        return true
    }

    func handleLocalVoiceConversation(_ transcript: SpeechTranscriptEvent) async -> Bool {
        guard LocalVoiceConversationCommand.parse(transcript.text) != nil else { return false }
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            userSessionAvailable
        else {
            return true
        }
        let boundSpeakerMatches = trustedTranscript.ownerPresenceVerified
            && trustedTranscript.ownerSpeakerProfile
            && trustedTranscript.speakerID == conversationSpeakerID
            && speakerIdentityModelFingerprint == conversationModelFingerprint
        let resetRequiresVerifiedSpeaker = conversationSpeakerID != nil
            || speakerIdentityCapability == .ready
        if resetRequiresVerifiedSpeaker,
           !(
               boundSpeakerMatches
                   || (
                       conversationSpeakerID == nil
                           && trustedTranscript.ownerSpeakerProfile
                           && trustedTranscript.ownerPresenceVerified
                   )
           )
        {
            logger.info("voice_conversation_reset_rejected reason=speaker_unverified")
            speakLocalVoiceUtility(
                LocalVoiceConversationCommand.unverifiedSpokenResponse,
                utility: "conversation",
                event: "unverified"
            )
            return true
        }
        clearVoiceConversationSession()
        if trustedTranscript.ownerSpeakerProfile && trustedTranscript.ownerPresenceVerified {
            chat.newConversation()
        }
        logger.info("voice_conversation_reset source=explicit_command")
        speakLocalVoiceUtility(
            LocalVoiceConversationCommand.spokenResponse,
            utility: "conversation",
            event: "reset"
        )
        return true
    }

    func handleLocalVoiceSpeakerIdentity(_ text: String, speakerID: String?) -> Bool {
        guard LocalVoiceSpeakerIdentityCommand.parse(text) != nil else { return false }
        let spokenIdentifier = LocalVoiceSpeakerIdentityCommand.spokenIdentifier(speakerID)
        logger.info(
            "local_speaker_identity recognized=\(spokenIdentifier != nil, privacy: .public) capability=\(self.speakerIdentityCapability.rawValue, privacy: .public)"
        )
        if let spokenIdentifier {
            speakLocalVoiceUtility(
                String(localized: "Te reconozco como \(spokenIdentifier).", bundle: .module),
                utility: "speaker_identity",
                event: "recognized"
            )
        } else if speakerIdentityCapability == .ready {
            speakLocalVoiceUtility(
                String(localized: "No pude reconocerte con suficiente confianza en este turno.", bundle: .module),
                utility: "speaker_identity",
                event: "not_recognized"
            )
        } else {
            speakLocalVoiceUtility(
                String(localized: "La identidad de voz aún no está configurada.", bundle: .module),
                utility: "speaker_identity",
                event: "unavailable"
            )
        }
        return true
    }

    func handleLocalVoiceApplicationContext(_ text: String) -> Bool {
        guard LocalVoiceApplicationContextCommand.parse(text) != nil else { return false }
        let applicationName = LocalVoiceApplicationContextCommand.sanitizedApplicationName(
            NSWorkspace.shared.frontmostApplication?.localizedName
        )
        logger.info("local_application_context resolved=\(applicationName != nil, privacy: .public)")
        speakLocalVoiceUtility(
            applicationName.map { String(localized: "La aplicación activa es \($0).", bundle: .module) }
                ?? String(localized: "No pude identificar la aplicación activa.", bundle: .module),
            utility: "application_context",
            event: applicationName == nil ? "unavailable" : "resolved"
        )
        return true
    }

    func handleLocalVoiceTimer(_ text: String) -> Bool {
        guard let command = LocalVoiceTimerCommand.parse(text) else { return false }
        switch command {
        case let .start(durationSeconds):
            let started = localVoiceTimer.start(durationSeconds: durationSeconds) { [weak self] in
                self?.announceLocalVoiceTimerCompletion()
            }
            guard started else {
                speakLocalVoiceUtility(
                    String(localized: "Ya hay un temporizador activo.", bundle: .module),
                    utility: "timer",
                    event: "rejected"
                )
                return true
            }
            logger.info("voice_timer_started duration_seconds=\(durationSeconds, privacy: .public)")
            speakLocalVoiceUtility(
                String(localized: "Temporizador iniciado por \(Self.spokenTimerDuration(durationSeconds)).", bundle: .module),
                utility: "timer",
                event: "started"
            )
        case .cancel:
            let cancelled = localVoiceTimer.cancel()
            logger.info("voice_timer_cancelled active=\(cancelled, privacy: .public)")
            speakLocalVoiceUtility(
                cancelled ? String(localized: "Temporizador cancelado.", bundle: .module) : String(localized: "No hay un temporizador activo.", bundle: .module),
                utility: "timer",
                event: cancelled ? "cancelled" : "missing"
            )
        case .pause:
            guard let remainingSeconds = localVoiceTimer.pause() else {
                speakLocalVoiceUtility(
                    localVoiceTimer.isPaused
                        ? String(localized: "El temporizador ya está pausado.", bundle: .module)
                        : String(localized: "No hay un temporizador en marcha.", bundle: .module),
                    utility: "timer",
                    event: localVoiceTimer.isPaused ? "already_paused" : "missing"
                )
                return true
            }
            logger.info("voice_timer_paused remaining_seconds=\(remainingSeconds, privacy: .public)")
            speakLocalVoiceUtility(
                String(localized: "Temporizador pausado. Quedaban \(Self.spokenTimerDuration(remainingSeconds)).", bundle: .module),
                utility: "timer",
                event: "paused"
            )
        case .resume:
            guard let remainingSeconds = localVoiceTimer.resume() else {
                speakLocalVoiceUtility(
                    localVoiceTimer.isActive
                        ? String(localized: "El temporizador ya está en marcha.", bundle: .module)
                        : String(localized: "No hay un temporizador pausado.", bundle: .module),
                    utility: "timer",
                    event: localVoiceTimer.isActive ? "already_running" : "missing"
                )
                return true
            }
            logger.info("voice_timer_resumed remaining_seconds=\(remainingSeconds, privacy: .public)")
            speakLocalVoiceUtility(
                String(localized: "Temporizador reanudado. Quedan \(Self.spokenTimerDuration(remainingSeconds)).", bundle: .module),
                utility: "timer",
                event: "resumed"
            )
        case .status:
            guard let remainingSeconds = localVoiceTimer.remainingSeconds else {
                speakLocalVoiceUtility(
                    String(localized: "No hay un temporizador activo.", bundle: .module),
                    utility: "timer",
                    event: "missing"
                )
                return true
            }
            logger.info("voice_timer_status remaining_seconds=\(remainingSeconds, privacy: .public)")
            let state = localVoiceTimer.isPaused ? String(localized: "El temporizador está pausado. Quedan", bundle: .module) : String(localized: "Quedan", bundle: .module)
            speakLocalVoiceUtility(
                "\(state) \(Self.spokenTimerDuration(remainingSeconds)).",
                utility: "timer",
                event: "status"
            )
        }
        return true
    }

    private func announceLocalVoiceTimerCompletion() {
        NSSound.beep()
        guard voiceState == .idle || voiceState == .completed || voiceState == .failed else {
            logger.info("voice_timer_completed announcement=beep_only")
            return
        }
        speakLocalVoiceUtility(
            String(localized: "El temporizador terminó.", bundle: .module),
            utility: "timer",
            event: "completed"
        )
    }

    func speakLocalVoiceUtility(_ text: String, utility: String, event: String) {
        voiceReplayBuffer.store(text, at: ProcessInfo.processInfo.systemUptime)
        voiceState = .speaking
        speakWithWakeWordIsolation(text) { [weak self] in
            guard let self, voiceState == .speaking else { return }
            voiceState = .completed
            logger.info(
                "local_voice_utility_announced utility=\(utility, privacy: .public) event=\(event, privacy: .public)"
            )
        }
    }

    private static func spokenTimerDuration(_ seconds: Int) -> String {
        let hours = seconds / 3_600
        let minutes = (seconds % 3_600) / 60
        let remainingSeconds = seconds % 60
        var parts: [String] = []
        if hours > 0 {
            parts.append(hours == 1 ? "1 hora" : "\(hours) horas")
        }
        if minutes > 0 {
            parts.append(minutes == 1 ? "1 minuto" : "\(minutes) minutos")
        }
        if remainingSeconds > 0 || parts.isEmpty {
            parts.append(
                remainingSeconds == 1 ? "1 segundo" : "\(remainingSeconds) segundos"
            )
        }
        guard parts.count > 1 else { return parts[0] }
        return parts.dropLast().joined(separator: ", ") + " y " + parts.last!
    }
}
