import Foundation
import XCTest
import Observation
@testable import DesktopCore

private actor ClickGate {
    var entered = false
    var open = false
    var waiters: [CheckedContinuation<Void,Never>] = []
    func wait() async { entered = true; if open { return }; await withCheckedContinuation { waiters.append($0) } }
    func didEnter() -> Bool { entered }
    func release() { open = true; let held = waiters; waiters = []; held.forEach { $0.resume() } }
}
private actor OwnerClickWire {
    var operation: RPCValue?
    var gate: ClickGate?
    var decisions = 0
    var inventories = 0
    var decisionGate: ClickGate?
    var loseDecision = false
    func gateNextStatus(_ gate: ClickGate) { self.gate = gate }
    func gateDecision(_ gate: ClickGate) { decisionGate = gate }
    func loseDecisionAcknowledgement() { loseDecision = true }
    func expiredOperation() throws {
        var row = try RestoredMemoryFixture.operation(phase:"failed").object!
        row["error_code"] = .string("memory_restore_review_expired"); operation = .object(row)
    }
    func inventoryCount() -> Int { inventories }
    func finishUnknown() throws { operation = try RestoredMemoryFixture.operation(phase:"publication_unknown",receipt:RestoredMemoryFixture.receipt(status:"unknown"),durable:true) }
    func decisionCount() -> Int { decisions }
    func request(_ method: String,_ params: [String:RPCValue]) async throws -> RPCValue {
        switch method {
        case "maintenance.status":
            let snapshot = RPCValue.object(["profile_id":.string("synthetic_restored"),"generation":.number(7),"closing":.bool(false),"operations":.array(operation.map { [$0] } ?? []),"previous_operations":.array([]),"blockers":.array([])])
            if let gate { self.gate = nil; await gate.wait() }; return snapshot
        case "maintenance.pending": return .object(["profile_id":.string("synthetic_restored"),"generation":.number(7),"pending":.array((operation == nil || decisions > 0 || operation?["phase"] != .string("awaiting_owner")) ? [] : [try RestoredMemoryFixture.approval()])])
        case "memory.restore.inventory": inventories += 1; return try RestoredMemoryFixture.operation(kind:method,phase:"complete",result:.object(["inventory":RestoredMemoryFixture.inventory()]))
        case "memory.restore.review": return try RestoredMemoryFixture.operation(kind:method,phase:"complete",result:.object(["review":RestoredMemoryFixture.review()]))
        case "memory.restore.copy_scope": let row = try RestoredMemoryFixture.operation(); operation = row; return row
        case "maintenance.decide":
            decisions += 1
            operation = try RestoredMemoryFixture.operation(phase:"working")
            if let decisionGate { await decisionGate.wait() }
            if loseDecision { throw RPCFailure.disconnected }
            return .object(["accepted":.bool(true)])
        default: throw RPCFailure.invalidResponse
        }
    }
}

private final class TicketChanges: @unchecked Sendable {
    private let lock = NSLock()
    private var changes = 0
    func changed() { lock.lock(); changes += 1; lock.unlock() }
    var count: Int { lock.lock(); defer { lock.unlock() }; return changes }
}
final class OwnerClickRaceTests: XCTestCase {
    @MainActor private func ready(_ wire: OwnerClickWire, owner: UUID = UUID()) async throws -> (RestoredMemoryStore, ProfileRuntimeGuard, MaintenanceApproval) {
        let runtime = ProfileRuntimeGuard(), store = RestoredMemoryStore(runtime:runtime,maxPolls:1,pause:{ try await Task.sleep(for:.seconds(60)) })
        store.configure(RestoredMemoryClient(runtime:runtime) { try await wire.request($0,$1) })
        store.setVisible(owner,visible:true); store.selectAgent("jarvis")
        await store.loadInventory(); store.selectSource(RestoredMemoryFixture.sourceID); await store.prepareReview(); await store.submit(); await store.refresh()
        return (store,runtime,try XCTUnwrap(store.approval))
    }
    private func entered(_ gate: ClickGate) async {
        let deadline = ContinuousClock.now.advanced(by:.seconds(5))
        while ContinuousClock.now < deadline { if await gate.didEnter() { return }; await Task.yield() }
        XCTFail("No entró la solicitud sintética gateada")
    }
    @MainActor func testValidOwnerClickDuringExistingReadonlyRefreshIsNotSilentlyLost() async throws {
        let wire = OwnerClickWire(), (store,_,ticket) = try await ready(wire)
        XCTAssertFalse(store.isRequesting); XCTAssertTrue(store.canDecide)
        let gate = ClickGate(); await wire.gateNextStatus(gate)
        let refresh = Task { await store.refresh() }; await entered(gate)
        XCTAssertTrue(store.isRefreshing); XCTAssertFalse(store.isRequesting)
        XCTAssertTrue(store.canDecide); XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.monitorPaused)
        XCTAssertFalse(store.mayPrepare); XCTAssertFalse(store.hasObservationTarget)
        await store.decide(allow:true,ticket:ticket)
        let count = await wire.decisionCount(); XCTAssertEqual(count,1)
        XCTAssertNil(store.approval)
        await gate.release(); await refresh.value
        XCTAssertNil(store.approval,"An older readonly reply must never restore a consumed card")
        XCTAssertNil(store.errorMessage); XCTAssertFalse(store.isRefreshing)
        await store.close()
    }
    @MainActor func testIdenticalRefreshKeepsTicketIdentityAndDecisionEnabledWithoutObservationChurn() async throws {
        let wire = OwnerClickWire(), (store,_,ticket) = try await ready(wire), counter = TicketChanges()
        withObservationTracking { _ = store.approval } onChange: { counter.changed() }
        let gate = ClickGate(); await wire.gateNextStatus(gate)
        let refresh = Task { await store.refresh() }; await entered(gate)
        XCTAssertEqual(store.approval?.id,ticket.id); XCTAssertTrue(store.canDecide)
        await gate.release(); await refresh.value
        XCTAssertEqual(store.approval?.id,ticket.id); XCTAssertTrue(store.canDecide)
        XCTAssertEqual(counter.count,0,"An identical poll should not remove/recreate the card")
        await store.close()
    }
    @MainActor func testDoubleClickSendsOneExactDecisionAndReportsControlBusy() async throws {
        let wire = OwnerClickWire(), (store,_,ticket) = try await ready(wire), gate = ClickGate()
        await wire.gateDecision(gate)
        let first = Task { await store.decide(allow:true,ticket:ticket) }; await entered(gate)
        await store.decide(allow:true,ticket:ticket)
        XCTAssertTrue(store.errorMessage?.contains("solicitud en curso") == true)
        let count = await wire.decisionCount(); XCTAssertEqual(count,1)
        await gate.release(); await first.value
        XCTAssertNil(store.approval); await store.close()
    }
    @MainActor func testLostDecisionACKKeepsVisibleUncertaintyAndStaleReadCannotEraseIt() async throws {
        let wire = OwnerClickWire(), (store,_,ticket) = try await ready(wire), gate = ClickGate()
        await wire.gateNextStatus(gate); let oldRead = Task { await store.refresh() }; await entered(gate)
        await wire.loseDecisionAcknowledgement(); await store.decide(allow:true,ticket:ticket)
        XCTAssertTrue(store.errorMessage?.contains("No se confirmó") == true)
        await gate.release(); await oldRead.value
        XCTAssertTrue(store.errorMessage?.contains("No se confirmó") == true)
        XCTAssertNil(store.approval); let count = await wire.decisionCount(); XCTAssertEqual(count,1)
        await store.refresh(); XCTAssertNil(store.approval)
        let after = await wire.decisionCount(); XCTAssertEqual(after,1)
        await store.close()
    }
    @MainActor func testExpiryIsAnExplicitVisibleOutcomeAndNeverSendsDecision() async throws {
        let wire = OwnerClickWire(), (store,_,ticket) = try await ready(wire)
        await store.decide(allow:true,ticket:ticket,now:Date().addingTimeInterval(121))
        XCTAssertTrue(store.errorMessage?.contains("caducó") == true); XCTAssertNil(store.approval)
        let count = await wire.decisionCount(); XCTAssertEqual(count,0)
        try await wire.expiredOperation(); await store.refresh()
        XCTAssertTrue(store.operationNotice?.contains("caducó") == true)
        XCTAssertTrue(store.errorMessage?.contains("caducó") == true)
        await store.close()
    }
    @MainActor func testStaleDisplayedTicketCannotDecideAndReportsWhy() async throws {
        let wire = OwnerClickWire(), (store,_,ticket) = try await ready(wire)
        try await wire.expiredOperation(); await store.refresh()
        await store.decide(allow:true,ticket:ticket)
        XCTAssertNotNil(store.errorMessage); let count = await wire.decisionCount(); XCTAssertEqual(count,0)
        await store.close()
    }
    @MainActor func testClosingRetiredRuntimeDropsLateDecisionReplyAndPreventsNewCalls() async throws {
        let wire = OwnerClickWire(), (store,runtime,ticket) = try await ready(wire), gate = ClickGate()
        await wire.gateDecision(gate); let decision = Task { await store.decide(allow:true,ticket:ticket) }; await entered(gate)
        runtime.retire(); await store.close(); await gate.release(); await decision.value
        XCTAssertNil(store.approval); XCTAssertFalse(store.canDecide)
        await store.decide(allow:true,ticket:ticket)
        let count = await wire.decisionCount(); XCTAssertEqual(count,1)
        XCTAssertTrue(store.errorMessage?.contains("ya no está abierto") == true)
    }
    @MainActor func testReadonlyRefreshAloneBlocksOtherReadsAndHandoffGateUntilSettledEvenAfterWindowCloses() async throws {
        let wire = OwnerClickWire(), owner = UUID(), (store,_,_) = try await ready(wire,owner:owner)
        try await wire.finishUnknown(); await store.refresh()
        XCTAssertTrue(store.operation?.settled == true); XCTAssertFalse(store.blocksInteraction)
        XCTAssertTrue(store.mayPrepare); XCTAssertTrue(store.hasObservationTarget)
        let before = await wire.inventoryCount(), gate = ClickGate()
        await wire.gateNextStatus(gate); let read = Task { await store.refresh() }; await entered(gate)
        store.setVisible(owner,visible:false)
        // ChatStore's send/change/reconnect/mayHandoff use this real gate.
        XCTAssertTrue(store.isRefreshing); XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.monitorPaused)
        XCTAssertFalse(store.mayPrepare); XCTAssertFalse(store.hasObservationTarget)
        await store.loadInventory(); let after = await wire.inventoryCount(); XCTAssertEqual(after,before)
        await gate.release(); await read.value
        XCTAssertFalse(store.isRefreshing); XCTAssertFalse(store.blocksInteraction); XCTAssertFalse(store.monitorPaused)
        XCTAssertTrue(store.mayPrepare); XCTAssertTrue(store.hasObservationTarget)
        await store.close()
    }

}
