import Foundation
import XCTest
@testable import DesktopCore

/// A cancelled clock can still own work. The fixture acknowledges its shutdown
/// only when released, without opening a file, device, account or network.
@MainActor
private final class MediaTimerWitness {
    private var sleepers: [CheckedContinuation<Void, Error>] = []
    private var startedWaiters: [CheckedContinuation<Void, Never>] = []
    private var cancelledWaiters: [CheckedContinuation<Void, Never>] = []
    private var nextWaiters: [CheckedContinuation<Void, Never>] = []
    private var settlementWaiters: [CheckedContinuation<Void, Never>] = []
    private var started = false
    private var cancelled = false
    private var released = false
    private var nextStarted = false
    private var settlementStarted = false
    var executionReturned = false
    var settlementReturned = false

    func sleep() async throws {
        try await withTaskCancellationHandler { () async throws -> Void in
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if released { continuation.resume(throwing: CancellationError()); return }
                sleepers.append(continuation)
                started = true
                let waiting = startedWaiters; startedWaiters.removeAll()
                for waiter in waiting { waiter.resume() }
            }
        } onCancel: {
            _ = Task { @MainActor in self.noteCancellation() }
        }
    }

    func waitUntilStarted() async {
        if !started { await withCheckedContinuation { startedWaiters.append($0) } }
    }

    func waitUntilCancelled() async {
        if !cancelled { await withCheckedContinuation { cancelledWaiters.append($0) } }
    }

    private func noteCancellation() {
        cancelled = true
        let waiting = cancelledWaiters; cancelledWaiters.removeAll()
        for waiter in waiting { waiter.resume() }
    }

    func markNextStarted() {
        nextStarted = true
        let waiting = nextWaiters; nextWaiters.removeAll()
        for waiter in waiting { waiter.resume() }
    }

    func waitUntilNextStarted() async {
        if !nextStarted { await withCheckedContinuation { nextWaiters.append($0) } }
    }

    func markSettlementStarted() {
        settlementStarted = true
        let waiting = settlementWaiters; settlementWaiters.removeAll()
        for waiter in waiting { waiter.resume() }
    }

    func waitUntilSettlementStarted() async {
        if !settlementStarted { await withCheckedContinuation { settlementWaiters.append($0) } }
    }

    func release() {
        released = true
        let waiting = sleepers; sleepers.removeAll()
        for sleeper in waiting { sleeper.resume(throwing: CancellationError()) }
    }
}

final class NativeMediaTimerCleanupTests: XCTestCase {
    @MainActor func testSuccessAndFailureRetainCustodyUntilCancelledTimerReturns() async throws {
        try await checkSettlement(.execution)
    }

    @MainActor func testExplicitCancellationJoinsTimerAsWellAsDecoder() async throws {
        try await checkSettlement(.cancel)
    }

    @MainActor func testResetJoinsTimerWithoutReplayingConsumedOperation() async throws {
        try await checkSettlement(.reset)
    }

    private enum Settlement: Equatable { case execution, cancel, reset }

    @MainActor private func checkSettlement(_ settlement: Settlement) async throws {
        for failPayload in [false, true] {
            let witness = MediaTimerWitness()
            let host = try NativeMediaExecutor(processingSeconds: 10, deadlineSleep: { _ in
                try await witness.sleep()
            })
            let payload = Data("{\\rtf1\\ansi TIMER CUSTODY}".utf8)
            let args = NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "timer.rtf")
            host.beforeEffect = { _ in }
            host.loadPayload = { _ in
                await witness.waitUntilStarted()
                if failPayload { throw NativeMediaIssue.sourceChanged }
                return payload
            }
            let execution = Task { @MainActor () -> Result<RPCValue, Error> in
                let result: Result<RPCValue, Error>
                do { result = .success(try await host.execute("media.inspect", arguments: args)) }
                catch { result = .failure(error) }
                witness.executionReturned = true
                return result
            }
            await witness.waitUntilCancelled()
            XCTAssertFalse(witness.executionReturned, "El resultado debe conservar la custodia del temporizador")

            let explicitSettlement = Task { @MainActor in
                witness.markSettlementStarted()
                switch settlement {
                case .execution: return
                case .cancel: await host.cancel(operationID: args["operation_id"]!.string!)
                case .reset: await host.reset()
                }
                witness.settlementReturned = true
            }
            await witness.waitUntilSettlementStarted()
            let nextArgs = NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "next.rtf")
            let next = Task { @MainActor () -> NativeMediaIssue? in
                witness.markNextStarted()
                do { _ = try await host.execute("media.inspect", arguments: nextArgs); return nil }
                catch { return error as? NativeMediaIssue }
            }
            await witness.waitUntilNextStarted()
            if settlement != .execution { XCTAssertFalse(witness.settlementReturned) }
            witness.release()
            let result = await execution.value
            await explicitSettlement.value
            let nextIssue = await next.value
            XCTAssertEqual(nextIssue, .busy, "El run siguiente debe esperar toda la limpieza anterior")
            switch result {
            case .success(let value):
                XCTAssertFalse(failPayload)
                XCTAssertEqual(settlement, .execution)
                XCTAssertEqual(value["succeeded"], .bool(true))
            case .failure(let error):
                if failPayload { XCTAssertEqual(error as? NativeMediaIssue, .sourceChanged) }
                else {
                    XCTAssertNotEqual(settlement, .execution)
                    XCTAssertEqual(error as? NativeMediaIssue, .cancelled)
                }
            }
            do { _ = try await host.execute("media.inspect", arguments: args); XCTFail("No repetir una operación consumida") }
            catch { XCTAssertEqual(error as? NativeMediaIssue, .alreadySubmitted) }
        }
    }
}
