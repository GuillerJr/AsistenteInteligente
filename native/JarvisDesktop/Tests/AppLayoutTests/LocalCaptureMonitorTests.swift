import AppKit
import AVFoundation
import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

@MainActor private final class CaptureMonitorDeadline: LocalVoiceCancellation {
    let action: @MainActor () -> Void
    var active = true
    init(_ action: @escaping @MainActor () -> Void) { self.action = action }
    func cancel() { active = false }
    func fire(evenIfCancelled: Bool = false) { if active || evenIfCancelled { action() } }
}
@MainActor private final class CaptureMonitorClock: LocalVoiceClock {
    var deadlines: [CaptureMonitorDeadline] = []
    func schedule(after seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any LocalVoiceCancellation {
        let value = CaptureMonitorDeadline(action); deadlines.append(value); return value
    }
}
@MainActor private final class CaptureMonitorState {
    var issue: LocalVoiceIssue?
    var checks = 0
    var failures: [LocalVoiceIssue] = []
}

final class LocalCaptureMonitorTests: XCTestCase {
    @MainActor func testNoPermissionQueriesBeforeExplicitStartAndRevocationStopsOnce() {
        let state = CaptureMonitorState(), clock = CaptureMonitorClock(), center = NotificationCenter(), workspace = NotificationCenter()
        let monitor = LocalCaptureMonitor(engine: NSObject(), center: center, workspaceCenter: workspace, clock: clock,
            permissionIssue: { state.checks += 1; return state.issue }, failed: { state.failures.append($0) })
        XCTAssertEqual(state.checks, 0); XCTAssertTrue(clock.deadlines.isEmpty)
        monitor.start(); XCTAssertEqual(state.checks, 1)
        state.issue = .microphoneDenied
        clock.deadlines.last?.fire()
        XCTAssertEqual(state.failures, [.microphoneDenied])
        clock.deadlines.last?.fire(evenIfCancelled: true)
        XCTAssertEqual(state.failures, [.microphoneDenied])
    }

    @MainActor func testOnlyExactEngineConfigurationChangeStopsTheCapture() async {
        let state = CaptureMonitorState(), center = NotificationCenter(), engine = NSObject(), another = NSObject()
        let monitor = LocalCaptureMonitor(engine: engine, center: center, workspaceCenter: NotificationCenter(), clock: CaptureMonitorClock(),
            permissionIssue: { nil }, failed: { state.failures.append($0) })
        monitor.start()
        center.post(name: .AVAudioEngineConfigurationChange, object: another)
        for _ in 0..<10 { await Task.yield() }
        XCTAssertTrue(state.failures.isEmpty)
        center.post(name: .AVAudioEngineConfigurationChange, object: engine)
        for _ in 0..<10 { await Task.yield() }
        XCTAssertEqual(state.failures, [.inputChanged])
    }

    @MainActor func testFocusSleepAndSessionRetirementNeverResumeAudio() async {
        for event in [NSApplication.didResignActiveNotification, NSWorkspace.willSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            let state = CaptureMonitorState(), center = NotificationCenter(), workspace = NotificationCenter(), clock = CaptureMonitorClock()
            let monitor = LocalCaptureMonitor(engine: NSObject(), center: center, workspaceCenter: workspace, clock: clock,
                permissionIssue: { nil }, failed: { state.failures.append($0) })
            monitor.start()
            (event == NSApplication.didResignActiveNotification ? center : workspace).post(name: event, object: nil)
            for _ in 0..<10 { await Task.yield() }
            XCTAssertEqual(state.failures, [.interrupted])
            clock.deadlines.last?.fire(evenIfCancelled: true)
            XCTAssertEqual(state.failures, [.interrupted])
        }
    }

    @MainActor func testExplicitStopWithdrawsObserversAndLateChecks() async {
        let state = CaptureMonitorState(), center = NotificationCenter(), workspace = NotificationCenter(), clock = CaptureMonitorClock(), engine = NSObject()
        let monitor = LocalCaptureMonitor(engine: engine, center: center, workspaceCenter: workspace, clock: clock,
            permissionIssue: { state.checks += 1; return .none }, failed: { state.failures.append($0) })
        monitor.start(); let originalChecks = state.checks
        monitor.stop()
        center.post(name: .AVAudioEngineConfigurationChange, object: engine)
        workspace.post(name: NSWorkspace.willSleepNotification, object: nil)
        clock.deadlines.last?.fire(evenIfCancelled: true)
        for _ in 0..<10 { await Task.yield() }
        XCTAssertTrue(state.failures.isEmpty); XCTAssertEqual(state.checks, originalChecks)
    }
}
