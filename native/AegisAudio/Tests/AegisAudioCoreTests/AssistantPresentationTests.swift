import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Coherent assistant presentation")
struct AssistantPresentationTests {
    private func status(
        daemon: DaemonConnectionState = .online,
        security: SecurityMonitorState = .intact,
        provider: ProviderReadinessState = .configured,
        local: Bool = false,
        voice: VoiceTurnState = .idle,
        code: String? = nil,
        interrupting: Bool = false,
        approval: Bool = false,
        browser: Bool = false,
        ambient: Bool = false,
        activity: [IPCSwarmAgentRole: Int] = [:]
    ) -> AssistantPresentation {
        .resolve(daemon: daemon, security: security, provider: provider,
                 localBrainAvailable: local, voice: voice, failureCode: code,
                 interrupting: interrupting, approvalPending: approval,
                 browserSelectionPending: browser, ambientListening: ambient, activity: activity)
    }

    @Test(arguments: [VoiceTurnState.idle, .listening, .followingUp, .submitting,
                      .processing, .speaking, .completed, .failed, .awaitingAuthorization])
    func securityOverridesEveryVoiceState(voice: VoiceTurnState) {
        let result = status(security: .compromised, voice: voice, approval: true, browser: true,
                            ambient: true, activity: [.planner: 1])
        #expect(result.kind == .securityBlocked)
        #expect(result.tone == .failure)
        #expect(!result.isAnimated)
        #expect(!result.detail.contains("disponible"))
        #expect(status(daemon: .securityFailure, voice: voice).kind == .securityBlocked)
    }

    @Test(arguments: [VoiceTurnState.idle, .completed, .listening, .failed])
    func disconnectedNeverLooksReady(voice: VoiceTurnState) {
        #expect(status(daemon: .offline, local: true, voice: voice, browser: true,
                       ambient: true).kind == .offline)
        #expect(status(daemon: .checking, voice: voice).kind == .connecting)
        #expect(status(daemon: .unknown, voice: voice).kind == .connecting)
    }

    @Test func securityMustBeVerified() {
        #expect(status(security: .unknown).kind == .securityChecking)
        #expect(status(security: .checking).kind == .securityChecking)
        #expect(status(security: .unavailable, approval: true).kind == .securityUnavailable)
    }

    @Test func readinessIsNotInferenceSuccess() {
        #expect(status(provider: .unknown).kind == .brainChecking)
        #expect(status(provider: .checking).kind == .brainChecking)
        #expect(status(provider: .missing).kind == .brainMissing)
        #expect(status(provider: .unavailable).kind == .brainUnavailable)
        #expect(status(provider: .missing, local: true).kind == .idle)
        #expect(status(provider: .configured).kind == .idle)
        #expect(!status().detail.contains("PRIVADO"))
    }

    @Test func failureStaysVisibleWhileSpoken() {
        let spoken = status(voice: .speaking, code: "remote_provider_unavailable", activity: [.planner: 2])
        #expect(spoken == status(voice: .failed, code: "remote_provider_unavailable"))
        #expect(spoken.kind == .failure)
        #expect(spoken.tone == .failure)
        #expect(!spoken.isAnimated)
    }

    @Test func interruptionDoesNotPretendToListen() {
        #expect(status(voice: .speaking, code: "job_timeout", interrupting: true).kind == .interrupting)
        #expect(status(security: .compromised, interrupting: true).kind == .securityBlocked)
    }

    @Test func pendingApprovalWinsOverBrowserAndActivity() {
        #expect(status(voice: .idle, approval: true, browser: true,
                       activity: [.planner: 3]).kind == .approval)
        #expect(status(voice: .awaitingAuthorization).kind == .approval)
        #expect(status(browser: true, activity: [.planner: 3]).kind == .browserSelection)
    }

    @Test func activeTurnWinsOverBackgroundTasks() {
        let voices: [(VoiceTurnState, AssistantPresentation.Kind)] = [
            (.listening, .listening), (.followingUp, .followingUp), (.submitting, .submitting),
            (.processing, .processing), (.speaking, .speaking),
        ]
        for (voice, kind) in voices {
            #expect(status(voice: voice, activity: [.planner: 1]).kind == kind)
        }
    }

    @Test func noGhostAgentsOrOverflow() {
        #expect(status(activity: [.planner: 0, .router: -1]).kind == .idle)
        #expect(status(voice: .completed, activity: [.planner: 1]).kind == .working)
        #expect(status(activity: [.planner: Int.max, .router: Int.min]).detail.hasPrefix("999 "))
    }

    @Test func completionDoesNotClaimExecution() {
        let result = status(voice: .completed)
        #expect(result.kind == .completed)
        #expect(result.title == "Turno finalizado")
        #expect(result.detail.contains("conocer el resultado"))
        #expect(!result.isAnimated)
    }

    @Test(arguments: ["job_cancelled", "job_superseded", "cancelled"])
    func cancellationDoesNotClaimRollback(code: String) {
        #expect(status(code: code).kind == .cancelled)
        #expect(status(code: code).detail.contains("no revierte"))
        #expect(status(voice: .processing, code: code).kind == .processing)
    }

    @Test func ambientOnlyWhenActuallyListening() {
        #expect(status(ambient: true).kind == .ambient)
        #expect(status(ambient: false).kind == .idle)
        #expect(!status().isAnimated)
        #expect(status(ambient: true).isAnimated)
    }

    @Test(arguments: ["job_status_unavailable", "job_timeout", "swarm_execution_timeout"])
    func uncertainResultsDoNotInviteBlindRetry(code: String) {
        #expect(status(voice: .failed, code: code).detail.contains("Comprueba si hubo cambios"))
    }

    @Test func unknownErrorDoesNotLeakPrivateText() {
        let secret = "private message token=supersecret /Users/alice"
        let result = status(voice: .failed, code: secret)
        #expect(!result.accessibilityDescription.contains(secret))
        #expect(result.title == "Solicitud no completada")
        #expect(result.compactTitle.count <= 24)
    }

    @Test func boundedSpanishLabelsAndStableEquality() {
        let samples = [status(), status(daemon: .checking), status(daemon: .offline),
                       status(security: .compromised), status(security: .checking),
                       status(security: .unavailable), status(provider: .missing),
                       status(provider: .unknown), status(provider: .unavailable),
                       status(approval: true), status(browser: true), status(ambient: true),
                       status(voice: .speaking), status(voice: .failed), status(voice: .completed)]
        for sample in samples {
            #expect(sample.compactTitle.count <= 24)
            #expect(sample.detail.count <= 150)
            #expect(!sample.symbol.isEmpty)
            #expect(sample.accessibilityDescription.contains(sample.title))
        }
        #expect(status(activity: [.planner: 0]) == status())
    }
}

@Suite("Assistant screen geometry")
struct AssistantPanelLayoutTests {
    @Test(arguments: [CGRect(x: 0, y: 0, width: 1440, height: 900),
                      CGRect(x: -1920, y: -400, width: 1920, height: 1080),
                      CGRect(x: 1000, y: 900, width: 400, height: 500)])
    func hudAlwaysFitsVisibleScreen(screen: CGRect) {
        let centered = AssistantPanelLayout.hudFrame(visibleFrame: screen)
        #expect(screen.contains(centered))
        #expect(centered.midX == screen.midX)
        #expect(centered.midY == screen.midY)
        let recovered = AssistantPanelLayout.hudFrame(
            visibleFrame: screen, previousFrame: CGRect(x: 9000, y: -9000, width: 560, height: 560))
        #expect(screen.contains(recovered))
        #expect(recovered.size == centered.size)
    }

    @Test func preservesUserPlacement() {
        let previous = CGRect(origin: CGPoint(x: 50, y: 100), size: AssistantPanelLayout.hudPreferredSize)
        #expect(AssistantPanelLayout.hudFrame(visibleFrame: CGRect(x: 0, y: 0, width: 1400, height: 900),
                                               previousFrame: previous) == previous)
    }

    @Test func compactHUDUsesOneSizeContract() {
        let frame = AssistantPanelLayout.hudFrame(
            visibleFrame: CGRect(x: 0, y: 0, width: 1440, height: 900),
            previousFrame: CGRect(x: 40, y: 60, width: 560, height: 560))
        #expect(frame.origin == CGPoint(x: 40, y: 60))
        #expect(frame.size == CGSize(width: 460, height: 480))
    }

    @Test func realNotchGeometryOnOffsetDisplay() throws {
        let screen = CGRect(x: -1512, y: 900, width: 1512, height: 982)
        let notch = try #require(AssistantPanelLayout.notch(
            screen: screen, left: CGRect(x: -1512, y: 1850, width: 660, height: 32),
            right: CGRect(x: -660, y: 1850, width: 660, height: 32), safeTop: 32, scale: 2))
        #expect(notch.width == 192)
        #expect(notch.height == 32)
        #expect(notch.frame.midX == screen.midX)
        #expect(notch.frame.maxY == screen.maxY)
        #expect(screen.contains(notch.frame))
    }

    @Test(arguments: [CGFloat(0), -1, .nan, .infinity])
    func rejectsInvalidScale(scale: CGFloat) {
        #expect(AssistantPanelLayout.notch(screen: CGRect(x: 0, y: 0, width: 1000, height: 800),
                                            left: CGRect(x: 0, y: 768, width: 400, height: 32),
                                            right: CGRect(x: 600, y: 768, width: 400, height: 32),
                                            safeTop: 32, scale: scale) == nil)
    }

    @Test func noFakeNotchOnExternalScreen() {
        #expect(AssistantPanelLayout.notch(screen: CGRect(x: 0, y: 0, width: 1920, height: 1080),
                                            left: .zero, right: .zero, safeTop: 0, scale: 1) == nil)
    }
}
