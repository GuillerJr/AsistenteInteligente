import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Mascot conversation")
struct MascotInteractionPresentationTests {
    @Test func lockSuppressesAllPrivateContentAndReview() {
        let result = MascotInteractionPresentation.resolve(
            sessionAvailable: false, state: .speaking,
            reviewMessage: "Private approval", question: "Private question",
            input: "Private input", response: "Private response", error: "Private error", notice: "Private notice")
        #expect(result.message == "Desbloquea tu Mac para conversar.")
        #expect(!result.requiresReview)
    }

    @Test func decisionAndClarificationTakePriorityOverSpeechAndNotices() {
        let approval = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .speaking, reviewMessage: "Consultar 10 correos",
            question: "¿Cuál?", response: "Una respuesta anterior", notice: "Batería baja")
        #expect(approval.requiresReview)
        #expect(approval.message == "Consultar 10 correos")
        let question = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .completed, question: "¿Para qué fecha?", response: "Anterior")
        #expect(question.requiresReview)
        #expect(question.message == "¿Para qué fecha?")
    }

    @Test func progressOnlyDescribesConfirmedStateAndNeverMakesUpAResult() {
        let waiting = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .processing, input: "Resume el documento")
        #expect(waiting.title == "Trabajando en tu solicitud")
        #expect(waiting.message == "Resume el documento")
        let streamed = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .speaking, input: "Resume", response: "El documento explica…")
        #expect(streamed.message == "El documento explica…")
        let idle = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .idle, activeRoles: [.vision, .planner])
        #expect(idle.title == "¿En qué te ayudo?")
    }

    @Test func noticesWaitUntilTheTurnEndsAndOutputIsBounded() {
        let busy = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .processing, input: "Mi solicitud", notice: "Aviso")
        #expect(busy.message == "Mi solicitud")
        let idle = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .completed, response: "Respuesta", notice: "Aviso")
        #expect(idle.message == "Aviso")
        let long = MascotInteractionPresentation.resolve(
            sessionAvailable: true, state: .completed, response: String(repeating: "á", count: 8_000))
        #expect(long.message.count == 2_000)
    }

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
    func conversationPanelNeverOverlapsCameraOrMenuBar(menuBarHeight: CGFloat) throws {
        let screen = CGRect(x: -1470, y: 100, width: 1470, height: 956)
        let notch = try #require(AssistantPanelLayout.notch(
            screen: screen, left: CGRect(x: -1470, y: 1024, width: 645.5, height: 32),
            right: CGRect(x: -645.5, y: 1024, width: 645.5, height: 32), safeTop: 32, scale: 2))
        let compact = AssistantPanelLayout.compactNotchFrame(notch, screen: screen, scale: 2, menuBarHeight: menuBarHeight)
        let expanded = AssistantPanelLayout.expandedNotchFrame(
            notch, screen: screen, scale: 2, menuBarHeight: menuBarHeight, preferredSize: CGSize(width: 340, height: 340))
        #expect(expanded.size == CGSize(width: 340, height: 340))
        for progress in [0.0, 0.1, 0.5, 0.9, 1.0] {
            let frame = AssistantPanelLayout.interpolatedNotchFrame(compact: compact, expanded: expanded, progress: progress)
            #expect(frame.maxY <= screen.maxY - menuBarHeight)
            #expect(screen.contains(frame))
            #expect(frame.midX == notch.frame.midX)
        }
    }
}
