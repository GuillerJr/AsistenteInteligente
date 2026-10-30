import Foundation
import XCTest
@testable import DesktopCore

private actor SyntheticLaunchPreparation {
    private var entered = false
    private var cancelled = false
    private var released = false
    private var entranceWaiters: [CheckedContinuation<Void, Never>] = []
    private var cancellationWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseWaiter: CheckedContinuation<Void, Never>?
    private let plan: ServiceLaunchPlan
    init(plan: ServiceLaunchPlan) { self.plan = plan }
    func prepare() async -> ServiceLaunchPlan {
        if released { return plan }
        entered = true
        let pending = entranceWaiters; entranceWaiters = []; pending.forEach { $0.resume() }
        await withTaskCancellationHandler(operation: {
            await withCheckedContinuation { releaseWaiter = $0 }
        }, onCancel: { Task { await self.recordCancellation() } })
        // Deliberately return success after cancellation to exercise the
        // service's own pre-effect gate, rather than trusting a cooperative IO.
        return plan
    }
    private func recordCancellation() {
        cancelled = true
        let pending = cancellationWaiters; cancellationWaiters = []; pending.forEach { $0.resume() }
    }
    func waitForEntrance() async {
        if entered { return }
        await withCheckedContinuation { entranceWaiters.append($0) }
    }
    func waitForCancellation() async {
        if cancelled { return }
        await withCheckedContinuation { cancellationWaiters.append($0) }
    }
    func release() {
        released = true
        releaseWaiter?.resume(); releaseWaiter = nil
    }
}

@MainActor private final class PreparedSyntheticChild: OwnedServiceChild {
    let processIdentifier: Int32 = 91_001
    var observedExit: ServiceChildExit?
    var isRunning: Bool { observedExit == nil }
    var signals: [Int32] = []
    func signal(_ value: Int32) -> Bool { signals.append(value); return true }
}

final class ServicePreparationTests: XCTestCase {
    @MainActor func testStopJoinsHashingAndAValidationReturningAfterCancellationCannotLaunch() async throws {
        let options = LaunchOptions(python: "/usr/bin/false", source: "/synthetic-core", state: "/private/tmp/synthetic-preparation", codex: "/usr/bin/false")
        let plan = try ServiceLaunchPlan(options, inheritedEnvironment: [:])
        let barrier = SyntheticLaunchPreparation(plan: plan)
        let child = PreparedSyntheticChild()
        var launches = 0
        let service = ServiceProcess(makeChild: { _ in XCTFail("No synchronous factory"); return child },
            makePreparedChild: { received, receivedPlan in
                XCTAssertEqual(received, options); XCTAssertEqual(receivedPlan.arguments, plan.arguments)
                launches += 1; return child
            }, prepareLaunch: { _ in await barrier.prepare() }, now: { 0 }, sleep: { _ in }, gracePeriod: 1)
        let start = Task { @MainActor in try await service.startVerified(options) }
        await barrier.waitForEntrance()
        var stopFinished = false
        let stop = Task { @MainActor in let result = await service.stop(); stopFinished = true; return result }
        await barrier.waitForCancellation()
        XCTAssertFalse(stopFinished, "Stop cannot acknowledge a still-running prelaunch validation")
        XCTAssertTrue(service.isStopping)
        XCTAssertEqual(launches, 0)
        XCTAssertThrowsError(try service.start(options))
        do { try await service.startVerified(options); XCTFail("No successor during cleanup") }
        catch { XCTAssertEqual(error as? ServiceProcessFailure, .cleanupUnconfirmed) }
        let sameStop = Task { @MainActor in await service.stop() }
        await barrier.release()
        let stopped = await stop.value, stoppedAgain = await sameStop.value
        XCTAssertTrue(stopped)
        XCTAssertTrue(stoppedAgain)
        do { try await start.value; XCTFail("Cancelled admission must not create a child") }
        catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertEqual(launches, 0)
        XCTAssertNil(service.ownedIdentity)
        XCTAssertFalse(service.isStopping)
        XCTAssertNil(service.lastStopReport, "No process was launched, so no native exit receipt is manufactured")
        try await service.startVerified(options)
        XCTAssertEqual(launches, 1)
        child.observedExit = ServiceChildExit(status: 0, wasSignalled: false)
        let finalStop = await service.stop()
        XCTAssertTrue(finalStop)
        XCTAssertEqual(child.signals, [])
    }
}
