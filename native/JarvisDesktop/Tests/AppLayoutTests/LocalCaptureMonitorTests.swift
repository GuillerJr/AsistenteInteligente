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
        XCTAssertEqual(seconds, 0.5)
        let value = CaptureMonitorDeadline(action); deadlines.append(value); return value
    }
}
/// Models the hop from a notification's internal queue to the main actor. Tests
/// choose delivery order, rather than depending on repeated Task.yield calls.
private final class CaptureMonitorDelivery: @unchecked Sendable {
    private let lock = NSLock()
    private var actions: [@MainActor @Sendable () -> Void] = []
    func enqueue(_ action: @escaping @MainActor @Sendable () -> Void) {
        lock.lock(); actions.append(action); lock.unlock()
    }
    @MainActor func drain() {
        lock.lock(); let pending = actions; actions = []; lock.unlock()
        for action in pending { action() }
    }
}

@MainActor private func configuration(uid: String = "mic-a", defaultUID: String = "mic-a",
                                      rate: Double = 48_000, channels: AVAudioChannelCount = 1) -> LocalCaptureInputConfiguration {
    LocalCaptureInputConfiguration(deviceUID: uid, defaultDeviceUID: defaultUID,
        format: AVAudioFormat(standardFormatWithSampleRate: rate, channels: channels)!)!
}
@MainActor private final class CaptureMonitorState {
    var issue: LocalVoiceIssue?
    var checks = 0
    var inputChecks = 0
    var current: LocalCaptureInputState? = .init(configuration: configuration(), isRunning: true)
    var failures: [LocalVoiceIssue] = []
}
@MainActor private final class CaptureMonitorFixture {
    let state = CaptureMonitorState(), clock = CaptureMonitorClock(), delivery = CaptureMonitorDelivery()
    let center = NotificationCenter(), workspace = NotificationCenter(), engine = NSObject()
    lazy var monitor = LocalCaptureMonitor(engine: engine, configuration: configuration(),
        inputState: { [state = self.state] in state.inputChecks += 1; return state.current },
        center: center, workspaceCenter: workspace, clock: clock,
        deliver: { [delivery = self.delivery] in delivery.enqueue($0) },
        permissionIssue: { [state = self.state] in state.checks += 1; return state.issue },
        failed: { [state = self.state] in state.failures.append($0) })
    func post(_ name: Notification.Name = .AVAudioEngineConfigurationChange, object: AnyObject? = nil) {
        center.post(name: name, object: object ?? engine)
    }
}

final class LocalCaptureMonitorTests: XCTestCase {
    @MainActor func testNoQueriesBeforeExplicitStartAndRevocationStopsOnce() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        XCTAssertEqual(fixture.state.checks, 0); XCTAssertEqual(fixture.state.inputChecks, 0)
        XCTAssertTrue(fixture.clock.deadlines.isEmpty)
        monitor.start(); XCTAssertEqual(fixture.state.checks, 1)
        fixture.state.issue = .microphoneDenied
        fixture.clock.deadlines.last?.fire()
        XCTAssertEqual(fixture.state.failures, [.microphoneDenied])
        fixture.clock.deadlines.last?.fire(evenIfCancelled: true)
        XCTAssertEqual(fixture.state.failures, [.microphoneDenied])
    }

    @MainActor func testStartupOrOutputNotificationDoesNotRetireUnchangedRunningInput() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        monitor.start()
        fixture.post(object: NSObject())
        fixture.delivery.drain()
        XCTAssertEqual(fixture.state.inputChecks, 1, "Another engine has no authority")
        fixture.post(); fixture.post()
        fixture.delivery.drain()
        XCTAssertTrue(fixture.state.failures.isEmpty)
        XCTAssertEqual(fixture.state.inputChecks, 3)
        XCTAssertEqual(fixture.clock.deadlines.count, 1, "Notifications do not rearm input or multiply timers")
        monitor.stop()
    }

    @MainActor func testActualDeviceUIDChangeStopsEvenWhenPCMFormatIsUnchanged() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        monitor.start()
        fixture.state.current = .init(configuration: configuration(uid: "mic-b"), isRunning: true)
        fixture.post(); fixture.delivery.drain()
        XCTAssertEqual(fixture.state.failures, [.inputChanged])
        let checks = fixture.state.inputChecks
        fixture.state.current = .init(configuration: configuration(), isRunning: true)
        fixture.clock.deadlines.last?.fire(evenIfCancelled: true)
        fixture.post(); fixture.delivery.drain()
        XCTAssertEqual(fixture.state.inputChecks, checks)
        XCTAssertEqual(fixture.state.failures, [.inputChanged], "No automatic recovery or restart")
    }

    @MainActor func testDefaultInputChangeIsDetectedBeforeEngineRetargets() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        monitor.start()
        fixture.state.current = .init(configuration: configuration(defaultUID: "mic-b"), isRunning: true)
        fixture.clock.deadlines.last?.fire()
        XCTAssertEqual(fixture.state.failures, [.inputChanged])
    }

    @MainActor func testRealSampleRateAndChannelChangesBothStopCapture() {
        for changed in [configuration(rate: 16_000), configuration(channels: 2)] {
            let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
            monitor.start()
            fixture.state.current = .init(configuration: changed, isRunning: true)
            fixture.post(); fixture.delivery.drain()
            XCTAssertEqual(fixture.state.failures, [.inputChanged])
        }
    }

    @MainActor func testStoppedEngineOrUnreadableInputCannotBeMistakenForStartupNoise() {
        let states: [LocalCaptureInputState?] = [.init(configuration: configuration(), isRunning: false), nil]
        for current in states {
            let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
            monitor.start(); fixture.state.current = current
            fixture.post(); fixture.delivery.drain()
            XCTAssertEqual(fixture.state.failures, [.inputChanged])
        }
    }

    @MainActor func testConfigurationWhichActuallyChangesDuringStartFailsBeforePolling() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        fixture.state.current = .init(configuration: configuration(rate: 16_000), isRunning: true)
        monitor.start()
        XCTAssertEqual(fixture.state.failures, [.inputChanged])
        XCTAssertTrue(fixture.clock.deadlines.isEmpty)
    }

    @MainActor func testNoObserverBeforeStartAndOldNotificationCannotReachNextEpoch() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        fixture.post(NSApplication.didResignActiveNotification)
        monitor.start(); fixture.delivery.drain()
        XCTAssertTrue(fixture.state.failures.isEmpty, "Construction does not queue an old focus event")
        fixture.post(NSApplication.didResignActiveNotification)
        let oldDeadline = fixture.clock.deadlines.last
        monitor.stop(); monitor.start()
        let checks = fixture.state.checks
        fixture.delivery.drain(); oldDeadline?.fire(evenIfCancelled: true)
        XCTAssertTrue(fixture.state.failures.isEmpty)
        XCTAssertEqual(fixture.state.checks, checks)
        fixture.post(NSApplication.didResignActiveNotification); fixture.delivery.drain()
        XCTAssertEqual(fixture.state.failures, [.interrupted], "New-epoch focus loss still stops capture")
    }

    @MainActor func testOldConfigurationEventCannotInspectNewEpoch() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        monitor.start(); fixture.post()
        monitor.stop(); monitor.start()
        fixture.state.current = .init(configuration: configuration(uid: "mic-b"), isRunning: true)
        let checks = fixture.state.inputChecks
        fixture.delivery.drain()
        XCTAssertTrue(fixture.state.failures.isEmpty)
        XCTAssertEqual(fixture.state.inputChecks, checks)
        fixture.clock.deadlines.last?.fire()
        XCTAssertEqual(fixture.state.failures, [.inputChanged], "The current epoch detects the real change itself")
    }

    @MainActor func testExplicitStopWithdrawsObserversAndPendingDelivery() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        monitor.start(); fixture.post()
        let checks = fixture.state.checks
        monitor.stop()
        fixture.delivery.drain(); fixture.post()
        fixture.workspace.post(name: NSWorkspace.willSleepNotification, object: nil)
        fixture.delivery.drain(); fixture.clock.deadlines.last?.fire(evenIfCancelled: true)
        XCTAssertTrue(fixture.state.failures.isEmpty); XCTAssertEqual(fixture.state.checks, checks)
    }

    @MainActor func testUnusedAudioDisconnectionDoesNotChangeTheSelectedInput() {
        let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
        monitor.start()
        fixture.post(AVCaptureDevice.wasDisconnectedNotification)
        fixture.delivery.drain()
        XCTAssertTrue(fixture.state.failures.isEmpty)
        fixture.state.current = nil
        fixture.post(AVCaptureDevice.wasDisconnectedNotification); fixture.delivery.drain()
        XCTAssertEqual(fixture.state.failures, [.inputChanged])
    }

    @MainActor func testFocusSleepAndSessionRetirementNeverResumeAudio() {
        for event in [NSApplication.didResignActiveNotification, NSWorkspace.willSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            let fixture = CaptureMonitorFixture(), monitor = fixture.monitor
            monitor.start()
            (event == NSApplication.didResignActiveNotification ? fixture.center : fixture.workspace).post(name: event, object: nil)
            fixture.delivery.drain()
            XCTAssertEqual(fixture.state.failures, [.interrupted])
            fixture.clock.deadlines.last?.fire(evenIfCancelled: true)
            XCTAssertEqual(fixture.state.failures, [.interrupted])
        }
    }

    @MainActor func testFingerprintIncludesSampleRepresentationAndRejectsMissingUID() {
        let float = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 1, interleaved: false)!
        let integer = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48_000, channels: 1, interleaved: false)!
        XCTAssertNotEqual(LocalCaptureInputConfiguration(deviceUID: "a", defaultDeviceUID: "a", format: float),
                          LocalCaptureInputConfiguration(deviceUID: "a", defaultDeviceUID: "a", format: integer))
        XCTAssertNil(LocalCaptureInputConfiguration(deviceUID: "", defaultDeviceUID: "a", format: float))
        XCTAssertNil(LocalCaptureInputConfiguration(deviceUID: "a", defaultDeviceUID: "", format: float))
    }
}
