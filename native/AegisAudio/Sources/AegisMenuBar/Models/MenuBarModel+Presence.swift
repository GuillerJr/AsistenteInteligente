import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Presence; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func transcriptWithOwnerPresence(
        _ transcript: SpeechTranscriptEvent
    ) async -> SpeechTranscriptEvent? {
        var presenceVerified = false
        var manuallyAuthorizedThisTurn = false
        if transcript.ownerSpeakerProfile, userSessionAvailable {
            let uptime = ProcessInfo.processInfo.systemUptime
            if ownerPresenceLease.isAuthorized(at: uptime) {
                presenceVerified = true
            } else if await ownerPresenceAuthenticator.verify(), userSessionAvailable {
                presenceVerified = ownerPresenceLease.authorize(
                    at: ProcessInfo.processInfo.systemUptime
                )
                manuallyAuthorizedThisTurn = presenceVerified
            }
            logger.info(
                "owner_presence_checked verified=\(presenceVerified, privacy: .public)"
            )
        }
        if manuallyAuthorizedThisTurn,
           let variant = pendingOwnerVoiceVariant,
           variant.captureID == transcript.captureID
        {
            pendingOwnerVoiceVariant = nil
            let store = ownerVoiceVariantStore
            let persisted = await Task.detached(priority: .background) {
                try? store.persist(variant)
            }.value
            if persisted != nil, let secret = ipcSecret {
                let notified = await Task.detached(priority: .background) {
                    guard let client = try? LocalIPCClient(secret: secret) else {
                        return false
                    }
                    return (
                        try? client.notifyBiometricTrainingSampleReady(
                            captureID: variant.captureID
                        ).ok
                    ) == true
                }.value
                logger.info(
                    "owner_voice_variant_notified accepted=\(notified, privacy: .public)"
                )
            }
            logger.info(
                "owner_voice_variant_validated persisted=\(persisted != nil, privacy: .public)"
            )
        } else if pendingOwnerVoiceVariant?.captureID == transcript.captureID {
            pendingOwnerVoiceVariant = nil
        }
        return SpeechTranscriptEvent(
            captureID: transcript.captureID,
            sequence: transcript.sequence,
            text: transcript.text,
            localeIdentifier: transcript.localeIdentifier,
            durationMilliseconds: transcript.durationMilliseconds,
            isFinal: transcript.isFinal,
            confidence: transcript.confidence,
            speakerID: transcript.speakerID,
            speakerConfidence: transcript.speakerConfidence,
            soleSpeakerProfile: presenceVerified && transcript.soleSpeakerProfile,
            ownerSpeakerProfile: presenceVerified && transcript.ownerSpeakerProfile,
            ownerPresenceVerified: presenceVerified
        )
    }

    func voiceConversationDecision(
        for transcript: SpeechTranscriptEvent,
        now: Date = Date()
    ) -> LocalVoiceConversationSession.Decision {
        let decision = LocalVoiceConversationSession.decision(
            storedConversationID: conversationID,
            lastUsedAt: conversationLastUsedAt,
            storedSpeakerID: conversationSpeakerID,
            storedModelFingerprint: conversationModelFingerprint,
            currentSpeakerID: transcript.speakerID,
            currentModelFingerprint: speakerIdentityModelFingerprint,
            ownerSpeakerProfile: transcript.ownerSpeakerProfile,
            ownerPresenceVerified: transcript.ownerPresenceVerified,
            speakerIdentityReady: speakerIdentityCapability == .ready,
            now: now,
            useActiveConversation: chat.conversationID != nil || chat.hasExplicitConversationSelection,
            activeConversationID: chat.conversationID
        )
        if decision.discardStoredSession {
            let capturedInput = voiceInputText
            clearVoiceConversationSession()
            voiceInputText = capturedInput
            logger.info("voice_conversation_reset source=invalid_or_expired")
        } else if !decision.persistAcceptedConversation {
            logger.info("voice_conversation_isolated reason=speaker_unverified")
        }
        return decision
    }

    func clearVoiceConversationSession() {
        voiceReplayBuffer.clear()
        speechOutput.clearReplay()
        voiceOutputText = nil
        voiceInputText = nil
        voiceHistoryConversationID = nil
        conversationID = nil
        conversationLastUsedAt = nil
        conversationSpeakerID = nil
        conversationModelFingerprint = nil
        UserDefaults.standard.removeObject(forKey: voiceConversationDefaultsKey)
        UserDefaults.standard.removeObject(forKey: voiceConversationLastUsedDefaultsKey)
        UserDefaults.standard.removeObject(forKey: voiceConversationSpeakerDefaultsKey)
        UserDefaults.standard.removeObject(forKey: voiceConversationModelDefaultsKey)
    }
}
