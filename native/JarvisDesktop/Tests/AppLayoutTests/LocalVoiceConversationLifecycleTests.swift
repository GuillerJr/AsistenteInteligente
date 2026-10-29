import AppKit
import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

@MainActor private final class ConversationLifecycleCancellation: LocalVoiceCancellation {
    let action: (@MainActor () -> Void)?
    var active = true
    init(_ action: (@MainActor () -> Void)? = nil) { self.action = action }
    func cancel() { active = false }
    func fire(evenIfCancelled: Bool = false) { if active || evenIfCancelled { action?() } }
}
@MainActor private final class ConversationLifecycleClock: LocalVoiceClock {
    var values: [ConversationLifecycleCancellation] = []
    func schedule(after seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any LocalVoiceCancellation {
        let value = ConversationLifecycleCancellation(action); values.append(value); return value
    }
}
@MainActor private final class ConversationLifecycleDriver: LocalVoiceDriver {
    var deferredPermission = false
    var captures: [@MainActor (LocalRecognitionEvent) -> Void] = []
    var playback: [@MainActor (LocalPlaybackEvent) -> Void] = []
    var permissionCallback: (@MainActor (LocalVoicePermissions) -> Void)?
    func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation {
        permissionCallback = completion
        if !deferredPermission { completion(.init(speech: .authorized, microphone: .authorized)) }
        return ConversationLifecycleCancellation()
    }
    func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation { completion(.success(())); return ConversationLifecycleCancellation() }
    func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws { captures.append(receive) }
    func finishRecognition() {}
    func cancelRecognition() {}
    func localVoices(localeIdentifier: String) -> [LocalVoiceChoice] { [.init(id: "com.apple.voice.fixture", name: "Sintética", language: "es-ES")] }
    func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws { playback.append(receive) }
    func stopPlayback() {}
}
@MainActor private final class ConversationLifecycleHost {
    var permissionIssue: LocalVoiceIssue?
    var permissionChecks = 0
    var pending: CheckedContinuation<LocalVoiceConversationReply, Error>?
    var holdModel = true
    var cancellations = 0
    func send() async throws -> LocalVoiceConversationReply {
        if holdModel { return try await withCheckedThrowingContinuation { pending = $0 } }
        return .init(runID: String(repeating: "a", count: 32), status: "completed", outcome: "known", answer: "Lectura sintética")
    }
    func cancel() -> Bool {
        cancellations += 1; pending?.resume(throwing: CancellationError()); pending = nil; return true
    }
}
@MainActor private struct ConversationLifecycleFixture {
    let driver = ConversationLifecycleDriver(), host = ConversationLifecycleHost()
    let center = NotificationCenter(), workspace = NotificationCenter(), clock = ConversationLifecycleClock()
    let context = LocalVoiceConversationContext(contextID: "synthetic-voice", revision: "runtime:7")
    let voice: LocalVoiceController
    let talk: LocalVoiceConversationController
    init(deferredPermission: Bool = false) {
        let voice = LocalVoiceController(driver: driver)
        self.voice = voice; driver.deferredPermission = deferredPermission
        let host = host, context = context
        let talk = LocalVoiceConversationController(voice: voice, currentContext: { context },
            submit: { _, _ in try await host.send() }, cancelRun: { _ in host.cancel() })
        self.talk = talk
        voice.onTranscript = { [weak talk] in _ = talk?.acceptTranscript($0) }
        voice.onCaptureCompletion = { [weak talk] in talk?.captureCompleted($0) }
        voice.onPlaybackCompletion = { [weak talk] in talk?.playbackCompleted($0) }
    }
    func monitor() -> MacLocalVoiceConversationLifecycle {
        let host = host
        return MacLocalVoiceConversationLifecycle(talk: talk, voice: voice, center: center, workspaceCenter: workspace, clock: clock,
            permissionIssue: { host.permissionChecks += 1; return host.permissionIssue })
    }
    func advance() async { for _ in 0..<50 { await Task.yield() } }
}

final class LocalVoiceConversationLifecycleTests: XCTestCase {
    @MainActor func testFocusLossDuringModelCancelsWithoutPlaybackOrRearming() async {
        let f = ConversationLifecycleFixture(); XCTAssertTrue(f.talk.start(context: f.context))
        let monitor = f.monitor()
        f.driver.captures[0](.final("Texto sintético")); await f.advance()
        XCTAssertEqual(f.talk.phase, .waitingForModel)
        f.center.post(name: NSApplication.didResignActiveNotification, object: nil); await f.advance()
        XCTAssertEqual(f.talk.stopReason, .interrupted); XCTAssertEqual(f.talk.phase, .idle)
        XCTAssertEqual(f.driver.captures.count, 1); XCTAssertTrue(f.driver.playback.isEmpty)
        XCTAssertEqual(f.host.cancellations, 1)
        monitor.close()
    }

    @MainActor func testPermissionRevocationDuringPlaybackClosesOutputAndNeverReopensInput() async {
        let f = ConversationLifecycleFixture(); f.host.holdModel = false
        XCTAssertTrue(f.talk.start(context: f.context)); let monitor = f.monitor()
        f.driver.captures[0](.final("Texto sintético")); await f.advance()
        XCTAssertEqual(f.talk.phase, .speaking)
        f.host.permissionIssue = .speechDenied
        f.clock.values.last?.fire(); await f.advance()
        XCTAssertEqual(f.talk.stopReason, .permissionRevoked); XCTAssertEqual(f.voice.phase, .idle)
        f.driver.playback[0](.finished)
        XCTAssertEqual(f.driver.captures.count, 1)
        monitor.close()
    }

    @MainActor func testAuthorizationPromptDoesNotCancelBeforeDecisionButSleepAlwaysDoes() async {
        let f = ConversationLifecycleFixture(deferredPermission: true)
        XCTAssertTrue(f.talk.start(context: f.context)); let monitor = f.monitor()
        XCTAssertEqual(f.voice.phase, .authorizing); XCTAssertEqual(f.host.permissionChecks, 0)
        f.center.post(name: NSApplication.didResignActiveNotification, object: nil); await f.advance()
        XCTAssertEqual(f.talk.phase, .listening)
        f.workspace.post(name: NSWorkspace.willSleepNotification, object: nil); await f.advance()
        XCTAssertEqual(f.talk.phase, .idle); XCTAssertEqual(f.talk.stopReason, .interrupted)
        f.driver.permissionCallback?(.init(speech: .authorized, microphone: .authorized))
        XCTAssertTrue(f.driver.captures.isEmpty)
        monitor.close()
    }

    @MainActor func testIdleSessionRetiresMonitorWithoutOngoingPermissionPolling() async {
        let f = ConversationLifecycleFixture(); XCTAssertTrue(f.talk.start(context: f.context))
        let monitor = f.monitor()
        f.talk.stop(); await f.advance()
        f.clock.values.last?.fire(); let checks = f.host.permissionChecks
        f.clock.values.last?.fire(evenIfCancelled: true)
        f.center.post(name: NSApplication.didResignActiveNotification, object: nil); await f.advance()
        XCTAssertEqual(f.host.permissionChecks, checks)
        XCTAssertEqual(f.host.cancellations, 1)
        monitor.close()
    }
}
