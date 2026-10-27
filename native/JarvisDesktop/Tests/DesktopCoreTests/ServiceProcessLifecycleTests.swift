import Foundation
import XCTest
import Darwin
@testable import DesktopCore

@MainActor
private final class SyntheticServiceChild: OwnedServiceChild {
    let processIdentifier: Int32
    var observedExit: ServiceChildExit?
    var isRunning: Bool { observedExit == nil }
    var signals: [Int32] = []
    var acceptsKill = true
    var exitOnTerm: ServiceChildExit?
    init(_ pid: Int32) { processIdentifier = pid }
    func signal(_ value: Int32) -> Bool {
        signals.append(value)
        if value == SIGTERM, let exitOnTerm { observedExit = exitOnTerm }
        return value == SIGKILL ? acceptsKill : true
    }
}

@MainActor
private final class ServiceManualClock {
    var time: TimeInterval = 0
    var sleeps = 0
    var cancelledSleepStates: [Bool] = []
    var waiting: [CheckedContinuation<Void, Error>] = []
    func sleep(_ seconds: TimeInterval) async throws {
        XCTAssertGreaterThan(seconds, 0)
        sleeps += 1; cancelledSleepStates.append(Task.isCancelled)
        try await withCheckedThrowingContinuation { waiting.append($0) }
    }
    func advance(_ seconds: TimeInterval) {
        time += seconds
        let continuations = waiting; waiting.removeAll()
        for value in continuations { value.resume() }
    }
    func failSleep() {
        let continuations = waiting; waiting.removeAll()
        for value in continuations { value.resume(throwing: CancellationError()) }
    }
}

final class ServiceProcessLifecycleTests: XCTestCase {
    private func recoveryFixture() throws -> (String, LaunchOptions) {
        let root = "/private/tmp/jarvis-crash-gate-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let descriptor = Darwin.open(root + "/profile.lock", O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw RPCFailure.configuration }
        Darwin.close(descriptor)
        let options = try LaunchOptions.parse(["--python", "/synthetic-python", "--source", "/synthetic-source",
                                               "--state", root, "--codex", "/synthetic-codex"])
        return (root, options)
    }

    @MainActor
    func testCrashRecoveryRequiresObservedExitSameProfileAndFreeKernelLease() async throws {
        let (root, options) = try recoveryFixture()
        defer { try? FileManager.default.removeItem(atPath: root) }
        let old = SyntheticServiceChild(42001), next = SyntheticServiceChild(42002)
        var count = 0
        let service = ServiceProcess(makeChild: { _ in count += 1; return count == 1 ? old : next },
            now: { 0 }, sleep: { _ in }, gracePeriod: 1)
        try service.start(options)
        try service.confirmReadyProfile(options)
        XCTAssertThrowsError(try service.prepareCrashRecovery(options)) {
            XCTAssertEqual($0 as? ServiceProcessFailure, .cleanupUnconfirmed)
        }
        let lease = Darwin.open(root + "/profile.lock", O_RDWR | O_NOFOLLOW)
        XCTAssertGreaterThanOrEqual(lease, 0)
        XCTAssertEqual(flock(lease, LOCK_EX | LOCK_NB), 0)
        old.observedExit = ServiceChildExit(status: SIGKILL, wasSignalled: true)
        let original = service.ownedIdentity
        XCTAssertThrowsError(try service.prepareCrashRecovery(options)) {
            XCTAssertEqual($0 as? ServiceProcessFailure, .recoveryResourcesBusy)
        }
        XCTAssertEqual(service.ownedIdentity, original)
        XCTAssertEqual(count, 1)
        Darwin.close(lease)
        let report = try service.prepareCrashRecovery(options)
        XCTAssertFalse(report.graceful)
        XCTAssertTrue(report.exitConfirmed)
        XCTAssertNotNil(report.warning)
        try service.start(options)
        XCTAssertEqual(count, 2)
        XCTAssertEqual(service.lastCrashRecoveryReport, report)
        XCTAssertEqual(old.signals, [])
        next.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        _ = await service.stop()
    }

    @MainActor
    func testCrashRecoveryDoesNotAdoptChangedPhysicalProfileOrLaunchConfiguration() async throws {
        let (root, options) = try recoveryFixture()
        defer { try? FileManager.default.removeItem(atPath: root); try? FileManager.default.removeItem(atPath: root + "-old") }
        let child = SyntheticServiceChild(42003)
        let service = ServiceProcess(makeChild: { _ in child }, now: { 0 }, sleep: { _ in }, gracePeriod: 1)
        try service.start(options)
        try service.confirmReadyProfile(options)
        child.observedExit = ServiceChildExit(status: 1, wasSignalled: false)
        let other = try LaunchOptions.parse(["--python", options.python, "--source", "/different-source", "--state", root, "--codex", options.codex])
        XCTAssertThrowsError(try service.prepareCrashRecovery(other)) {
            XCTAssertEqual($0 as? ServiceProcessFailure, .recoveryContextChanged)
        }
        try FileManager.default.moveItem(atPath: root, toPath: root + "-old")
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        XCTAssertThrowsError(try service.prepareCrashRecovery(options)) {
            XCTAssertEqual($0 as? ServiceProcessFailure, .recoveryContextChanged)
        }
        XCTAssertNotNil(service.ownedIdentity)
        XCTAssertNil(service.lastCrashRecoveryReport)
        _ = await service.stop()
    }

    @MainActor
    func testCrashRecoveryCannotLoopPastTwoConfirmedExitsWithinMinute() async throws {
        let (root, options) = try recoveryFixture()
        defer { try? FileManager.default.removeItem(atPath: root) }
        let children = [SyntheticServiceChild(42004), SyntheticServiceChild(42005), SyntheticServiceChild(42006)]
        var count = 0
        let service = ServiceProcess(makeChild: { _ in defer { count += 1 }; return children[count] },
            now: { 0 }, sleep: { _ in }, gracePeriod: 1)
        for index in 0..<3 {
            try service.start(options)
            try service.confirmReadyProfile(options)
            children[index].observedExit = ServiceChildExit(status: 1, wasSignalled: false)
            if index < 2 { _ = try service.prepareCrashRecovery(options) }
            else {
                XCTAssertThrowsError(try service.prepareCrashRecovery(options)) {
                    XCTAssertEqual($0 as? ServiceProcessFailure, .recoveryThrottled)
                }
            }
        }
        XCTAssertEqual(count, 3)
        XCTAssertNotNil(service.ownedIdentity)
        _ = await service.stop()
    }

    @MainActor
    private func until(_ condition: () -> Bool) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        while !condition() {
            guard ProcessInfo.processInfo.systemUptime < deadline else {
                XCTFail("No se alcanzó la frontera sintética de la prueba")
                throw ServiceProcessFailure.cleanupUnconfirmed
            }
            await Task.yield()
        }
    }

    private func options() throws -> LaunchOptions {
        try LaunchOptions.parse(["--python", "/synthetic-python", "--source", "/synthetic-source",
                                 "--state", "/private/tmp/synthetic-state", "--codex", "/synthetic-codex"])
    }

    @MainActor
    func testRejectedKillRetainsOwnerAndBlocksStartUntilExitIsObserved() async throws {
        let first = SyntheticServiceChild(31001), replacement = SyntheticServiceChild(31002)
        first.acceptsKill = false
        let clock = ServiceManualClock()
        var launches = 0
        let service = ServiceProcess(makeChild: { _ in
            launches += 1; return launches == 1 ? first : replacement
        }, now: { clock.time }, sleep: { try await clock.sleep($0) }, gracePeriod: 35)
        try service.start(options())
        let stopping = Task { @MainActor in await service.stop() }
        try await until { !clock.waiting.isEmpty }
        clock.advance(35)
        let confirmed = await stopping.value
        XCTAssertFalse(confirmed)
        XCTAssertTrue(service.isRunning && service.requiresCleanupConfirmation)
        XCTAssertEqual(service.lastStopReport?.processIdentifier, first.processIdentifier)
        XCTAssertEqual(service.lastStopReport?.killAccepted, false)
        XCTAssertNil(service.lastStopReport?.exit)
        XCTAssertThrowsError(try service.start(options())) {
            XCTAssertEqual($0 as? ServiceProcessFailure, .cleanupUnconfirmed)
        }
        XCTAssertEqual(launches, 1)
        // An explicit retry still owns the same child, never nil -> true.
        let retry = Task { @MainActor in await service.stop() }
        try await until { !clock.waiting.isEmpty }
        first.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        clock.advance(0.05)
        let retryConfirmed = await retry.value
        XCTAssertTrue(retryConfirmed)
        XCTAssertFalse(service.requiresCleanupConfirmation)
        try service.start(options())
        XCTAssertEqual(launches, 2)
        XCTAssertTrue(service.isRunning)
        replacement.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        _ = await service.stop()
    }

    @MainActor
    func testSignalAcknowledgementIsNotExitAndLaterForcedExitIsNotGraceful() async throws {
        let child = SyntheticServiceChild(31003), clock = ServiceManualClock()
        let service = ServiceProcess(makeChild: { _ in child }, now: { clock.time },
                                     sleep: { try await clock.sleep($0) }, gracePeriod: 35)
        try service.start(options())
        let stopping = Task { @MainActor in await service.stop() }
        try await until { !clock.waiting.isEmpty }
        clock.advance(35)
        let confirmed = await stopping.value
        XCTAssertFalse(confirmed)
        XCTAssertEqual(child.signals, [SIGTERM, SIGKILL])
        XCTAssertEqual(service.lastStopReport?.killAccepted, true)
        XCTAssertTrue(service.requiresCleanupConfirmation)
        XCTAssertThrowsError(try service.start(options()))
        child.observedExit = ServiceChildExit(status: SIGKILL, wasSignalled: true)
        let laterConfirmed = await service.stop()
        XCTAssertTrue(laterConfirmed)
        XCTAssertEqual(child.signals, [SIGTERM, SIGKILL])
        XCTAssertEqual(service.lastStopReport?.graceful, false)
        XCTAssertNotNil(service.lastStopReport?.warning)
    }

    @MainActor
    func testConcurrentStopsShareOneOwnerAndCannotForgetAReplacement() async throws {
        let child = SyntheticServiceChild(31004), replacement = SyntheticServiceChild(31005)
        let clock = ServiceManualClock()
        var launches = 0, entered = 0
        let service = ServiceProcess(makeChild: { _ in
            launches += 1; return launches == 1 ? child : replacement
        }, now: { clock.time }, sleep: { try await clock.sleep($0) }, gracePeriod: 35)
        try service.start(options())
        let first = Task { @MainActor in entered += 1; return await service.stop() }
        let second = Task { @MainActor in entered += 1; return await service.stop() }
        try await until { entered == 2 && !clock.waiting.isEmpty }
        XCTAssertTrue(service.isStopping)
        XCTAssertThrowsError(try service.start(options()))
        XCTAssertEqual(child.signals, [SIGTERM])
        child.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        clock.advance(0.05)
        let firstConfirmed = await first.value
        XCTAssertTrue(firstConfirmed)
        try service.start(options())
        let secondConfirmed = await second.value
        XCTAssertTrue(secondConfirmed)
        XCTAssertTrue(service.isRunning)
        XCTAssertEqual(launches, 2)
        XCTAssertEqual(replacement.signals, [])
        replacement.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        _ = await service.stop()
    }

    @MainActor
    func testCancellingAWaiterDoesNotShortenTheOwnedGraceOrForceEarlyKill() async throws {
        let child = SyntheticServiceChild(31006), clock = ServiceManualClock()
        let service = ServiceProcess(makeChild: { _ in child }, now: { clock.time },
                                     sleep: { try await clock.sleep($0) }, gracePeriod: 35)
        try service.start(options())
        let waiter = Task { @MainActor in await service.stop() }
        try await until { !clock.waiting.isEmpty }
        waiter.cancel()
        clock.advance(34)
        try await until { clock.sleeps == 2 }
        XCTAssertEqual(child.signals, [SIGTERM])
        XCTAssertEqual(clock.cancelledSleepStates, [false, false])
        clock.advance(1)
        let confirmed = await waiter.value
        XCTAssertFalse(confirmed)
        XCTAssertEqual(child.signals, [SIGTERM, SIGKILL])
        XCTAssertTrue(service.requiresCleanupConfirmation)
        child.observedExit = ServiceChildExit(status: SIGKILL, wasSignalled: true)
        _ = await service.stop()
    }

    @MainActor
    func testInterruptedOwnedSleepRetainsChildWithoutEscalatingSignal() async throws {
        let child = SyntheticServiceChild(31007), clock = ServiceManualClock()
        let service = ServiceProcess(makeChild: { _ in child }, now: { clock.time },
                                     sleep: { try await clock.sleep($0) }, gracePeriod: 35)
        try service.start(options())
        let stopping = Task { @MainActor in await service.stop() }
        try await until { !clock.waiting.isEmpty }
        clock.failSleep()
        let confirmed = await stopping.value
        XCTAssertFalse(confirmed)
        XCTAssertEqual(child.signals, [SIGTERM])
        XCTAssertTrue(service.requiresCleanupConfirmation)
        XCTAssertThrowsError(try service.start(options()))
        child.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        _ = await service.stop()
    }

    @MainActor
    func testObservedFailureIsReportedWithoutCallingItGracefulCleanup() async throws {
        let child = SyntheticServiceChild(31008)
        child.exitOnTerm = ServiceChildExit(status: 1, wasSignalled: false)
        let service = ServiceProcess(makeChild: { _ in child }, now: { 0 },
                                     sleep: { _ in XCTFail("No hay espera si la salida fue observada") },
                                     gracePeriod: 35)
        try service.start(options())
        let confirmed = await service.stop()
        XCTAssertTrue(confirmed)
        XCTAssertEqual(service.lastStopReport?.graceful, false)
        XCTAssertNotNil(service.lastStopReport?.warning)
        XCTAssertEqual(child.signals, [SIGTERM])
        let repeated = await service.stop()
        XCTAssertTrue(repeated)
        XCTAssertEqual(service.lastStopReport?.graceful, false)
    }

    @MainActor
    func testGracefulObservedExitAndNeverStartedServiceHaveNoWarning() async throws {
        let child = SyntheticServiceChild(31009)
        child.exitOnTerm = ServiceChildExit(status: 0, wasSignalled: false)
        let service = ServiceProcess(makeChild: { _ in child }, now: { 0 },
                                     sleep: { _ in XCTFail("La salida ya fue observada") }, gracePeriod: 35)
        let noChild = await service.stop()
        XCTAssertTrue(noChild)
        XCTAssertNil(service.lastStopReport)
        try service.start(options())
        let confirmed = await service.stop()
        XCTAssertTrue(confirmed)
        XCTAssertEqual(service.lastStopReport?.graceful, true)
        XCTAssertNil(service.lastStopReport?.warning)
        XCTAssertFalse(service.requiresCleanupConfirmation)
    }
}

/// Simulates precisely the stale Foundation cache; no PID from this stub is
/// ever signalled by the test. The kernel probe and emit recorder are separate.
private final class StaleFoundationProcess: Process, @unchecked Sendable {
    private let identifier: Int32
    init(_ identifier: Int32) { self.identifier = identifier; super.init() }
    override var processIdentifier: Int32 { identifier }
    override var isRunning: Bool { true }
    override var terminationStatus: Int32 { 0 }
    override var terminationReason: Process.TerminationReason { .exit }
}

extension ServiceProcessLifecycleTests {
    @MainActor
    func testStaleFoundationCacheWithNoKernelChildNeverSignalsOrInventsExitZero() async throws {
        // A real disposable direct child, already reaped. It has no account,
        // files, network, or inherited environment. Never use waitUntilExit.
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/usr/bin/true")
        child.environment = [:]
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
        let pid = child.processIdentifier
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        while true {
            var status: Int32 = 0
            let result = waitpid(pid, &status, WNOHANG)
            if result == pid || (result == -1 && errno == ECHILD) { break }
            guard ProcessInfo.processInfo.systemUptime < deadline else {
                XCTFail("El fixture propio no salió dentro de su presupuesto")
                throw ServiceProcessFailure.cleanupUnconfirmed
            }
            await Task.yield()
        }
        let cache = StaleFoundationProcess(pid)
        XCTAssertTrue(cache.isRunning)
        var signals: [Int32] = []
        let adapter = FoundationServiceChild(child: cache, emit: { signals.append($0); return true })
        XCTAssertFalse(adapter.isRunning)
        XCTAssertNil(adapter.observedExit?.status)
        XCTAssertNil(adapter.observedExit?.wasSignalled)
        XCTAssertFalse(adapter.signal(SIGTERM))
        XCTAssertFalse(adapter.signal(SIGKILL))
        XCTAssertEqual(signals, [])
        let service = ServiceProcess(makeChild: { _ in adapter }, now: { 0 },
                                     sleep: { _ in XCTFail("No se espera un hijo inexistente") },
                                     gracePeriod: 35)
        try service.start(options())
        let confirmed = await service.stop()
        XCTAssertTrue(confirmed)
        XCTAssertEqual(service.lastStopReport?.graceful, false)
        XCTAssertNil(service.lastStopReport?.exit?.status)
        XCTAssertNotNil(service.lastStopReport?.warning)
        XCTAssertEqual(signals, [])
    }

    @MainActor
    func testKernelIdentityChangeRejectsReusedPIDWithoutReapingOrSignallingIt() {
        let expected = ServiceKernelIdentity(pid: 31010, parent: getpid(), startSeconds: 1,
                                             startMicroseconds: 2)
        let replacement = ServiceKernelIdentity(pid: 31010, parent: getpid(), startSeconds: 3,
                                                startMicroseconds: 4)
        var waited = false, signalled = false
        let kernel = ServiceKernelOwner(pid: 31010, expected: expected,
                                        identity: { replacement },
                                        wait: { waited = true; return .owned },
                                        reportedExit: { nil }, emit: { _ in signalled = true; return true })
        XCTAssertFalse(kernel.signal(SIGTERM))
        XCTAssertFalse(kernel.signal(SIGKILL))
        XCTAssertFalse(waited)
        XCTAssertFalse(signalled)
        XCTAssertNil(kernel.observedExit?.status)
    }

    @MainActor
    func testKernelIdentityIsCheckedAgainImmediatelyBeforeSignal() {
        let expected = ServiceKernelIdentity(pid: 31011, parent: getpid(), startSeconds: 1,
                                             startMicroseconds: 2)
        let replacement = ServiceKernelIdentity(pid: 31011, parent: getpid(), startSeconds: 3,
                                                startMicroseconds: 4)
        var queries = 0, emitted = 0
        let kernel = ServiceKernelOwner(pid: 31011, expected: expected, identity: {
            queries += 1; return queries == 1 ? expected : replacement
        }, wait: { .owned }, reportedExit: { nil }, emit: { _ in emitted += 1; return true })
        XCTAssertFalse(kernel.signal(SIGTERM))
        XCTAssertEqual(queries, 2)
        XCTAssertEqual(emitted, 0)
        XCTAssertNil(kernel.observedExit?.status)
    }

    @MainActor
    func testMissingKernelIdentityOrChildRelationshipNeverGrantsSignalAuthority() {
        let expected = ServiceKernelIdentity(pid: 31012, parent: getpid(), startSeconds: 1,
                                             startMicroseconds: 2)
        for hasIdentity in [false, true] {
            var emitted = 0
            let kernel = ServiceKernelOwner(pid: 31012, expected: expected,
                                            identity: { hasIdentity ? expected : nil },
                                            wait: { hasIdentity ? .unknown : .owned },
                                            reportedExit: { nil }, emit: { _ in emitted += 1; return true })
            XCTAssertFalse(kernel.signal(SIGTERM))
            XCTAssertFalse(kernel.signal(SIGKILL))
            XCTAssertEqual(emitted, 0)
            XCTAssertNil(kernel.observedExit)
        }
    }

    @MainActor
    func testActualKernelWaitStatusIsPreservedWhenThisOwnerReapsTheChild() {
        let expected = ServiceKernelIdentity(pid: 31013, parent: getpid(), startSeconds: 1,
                                             startMicroseconds: 2)
        let kernel = ServiceKernelOwner(pid: 31013, expected: expected,
                                        identity: { expected }, wait: { .exited(7 << 8) },
                                        reportedExit: { nil }, emit: { _ in XCTFail("No señal tras salida"); return false })
        XCTAssertEqual(kernel.observedExit, ServiceChildExit(status: 7, wasSignalled: false))
        XCTAssertFalse(kernel.signal(SIGTERM))
    }
}
