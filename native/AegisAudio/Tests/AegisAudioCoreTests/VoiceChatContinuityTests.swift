import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Voice chat continuity")
struct VoiceChatContinuityTests {
    @Test(arguments: [false, true])
    func unverifiedVoiceNeverReceivesTheSelectedChat(identityReady: Bool) {
        let selectedChat = UUID()
        let decision = LocalVoiceConversationSession.decision(
            storedConversationID: nil, lastUsedAt: nil, storedSpeakerID: nil, storedModelFingerprint: nil,
            currentSpeakerID: "owner", currentModelFingerprint: String(repeating: "a", count: 64),
            ownerSpeakerProfile: false, ownerPresenceVerified: false, speakerIdentityReady: identityReady,
            now: Date(), useActiveConversation: true, activeConversationID: selectedChat)
        #expect(decision.conversationID == nil)
        #expect(decision.boundSpeakerID == nil)
    }

    @Test(arguments: [false, true])
    func verifiedOwnerHonorsSelectedChatAndExplicitNewChat(fresh: Bool) {
        let selectedChat = fresh ? nil : UUID()
        let decision = LocalVoiceConversationSession.decision(
            storedConversationID: UUID(), lastUsedAt: Date(), storedSpeakerID: "owner",
            storedModelFingerprint: String(repeating: "a", count: 64), currentSpeakerID: "owner",
            currentModelFingerprint: String(repeating: "a", count: 64),
            ownerSpeakerProfile: true, ownerPresenceVerified: true, speakerIdentityReady: true,
            now: Date(), useActiveConversation: true, activeConversationID: selectedChat)
        #expect(decision.conversationID == selectedChat)
        #expect(decision.persistAcceptedConversation)
        #expect(decision.boundSpeakerID == "owner")
    }

    @Test func missingOwnerPresenceCannotUseActiveChat() {
        let decision = LocalVoiceConversationSession.decision(
            storedConversationID: nil, lastUsedAt: nil, storedSpeakerID: nil, storedModelFingerprint: nil,
            currentSpeakerID: "owner", currentModelFingerprint: String(repeating: "a", count: 64),
            ownerSpeakerProfile: true, ownerPresenceVerified: false, speakerIdentityReady: true,
            now: Date(), useActiveConversation: true, activeConversationID: UUID())
        #expect(decision.conversationID == nil)
        #expect(!decision.persistAcceptedConversation)
    }

    @Test(arguments: [CGFloat(32), 38.25, 48])
    func mascotPanelNeverOverlapsCameraOrMenuBar(menuBarHeight: CGFloat) throws {
        let screen = CGRect(x: -1470, y: 100, width: 1470, height: 956)
        let notch = try #require(AssistantPanelLayout.notch(
            screen: screen, left: CGRect(x: -1470, y: 1024, width: 645.5, height: 32),
            right: CGRect(x: -645.5, y: 1024, width: 645.5, height: 32), safeTop: 32, scale: 2))
        let compact = AssistantPanelLayout.compactNotchFrame(notch, screen: screen, scale: 2, menuBarHeight: menuBarHeight)
        let expanded = AssistantPanelLayout.expandedNotchFrame(
            notch, screen: screen, scale: 2, menuBarHeight: menuBarHeight, preferredSize: CGSize(width: 220, height: 180))
        #expect(expanded.size == CGSize(width: 220, height: 180))
        for progress in [0.0, 0.1, 0.5, 0.9, 1.0] {
            let frame = AssistantPanelLayout.interpolatedNotchFrame(compact: compact, expanded: expanded, progress: progress)
            #expect(frame.maxY <= screen.maxY - menuBarHeight)
            #expect(screen.contains(frame))
            #expect(frame.midX == notch.frame.midX)
        }
    }
}
