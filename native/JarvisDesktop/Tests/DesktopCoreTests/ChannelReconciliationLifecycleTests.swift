import Foundation
import XCTest
@testable import DesktopCore

private actor ReconciliationLifecycleWire {
    private var row: RPCValue?
    private var includeTicket = true
    private var holdRead = false
    private var readEntered = false
    private var failRead = false
    private var held: CheckedContinuation<Void, Never>?
    private var holdReview = false
    private var reviewEntered = false
    private var reviewHeld: CheckedContinuation<Void, Never>?
    private var heldDecision: CheckedContinuation<Void, Never>?
    private var holdDecision = false
    private var decisionEntered = false
    private let reviewChanges: [String: RPCValue]
    private var pendingCalls = 0
    private var decisions = 0
    private var reviewCalls = 0

    init(reviewChanges: [String: RPCValue] = [:]) { self.reviewChanges = reviewChanges }
    func armRead(fail: Bool) { holdRead = true; failRead = fail; readEntered = false }
    func armReview() { holdReview = true; reviewEntered = false }
    func armDecision() { holdDecision = true; decisionEntered = false }
    func enteredRead() -> Bool { readEntered }
    func enteredReview() -> Bool { reviewEntered }
    func enteredDecision() -> Bool { decisionEntered }
    func counts() -> (Int, Int, Int) { (pendingCalls, decisions, reviewCalls) }
    func releaseRead() { held?.resume(); held = nil }
    func releaseReview() { reviewHeld?.resume(); reviewHeld = nil }
    func releaseDecision() { heldDecision?.resume(); heldDecision = nil }
    private func custody(_ live: Bool) throws -> RPCValue {
        var value = try ChannelReconciliationFixture.operation(live ? "cleanup_pending" : "publication_unknown",
            receipt: ChannelReconciliationFixture.receipt(status: "unknown", changes: ["cleanup_pending": .bool(true)])).object!
        value["cleanup_owner_pending"] = .bool(live)
        return .object(value)
    }
    func losePublicationWithCustody() throws { row = try custody(true); includeTicket = false }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        switch method {
        case "telegram.restore.review":
            reviewCalls += 1
            if holdReview {
                holdReview = false; reviewEntered = true
                await withCheckedContinuation { reviewHeld = $0 }
            }
            return try ChannelReconciliationFixture.review(reviewChanges)
        case "telegram.restore.reconcile": row = try ChannelReconciliationFixture.operation(); return row!
        case "maintenance.status":
            let snapshot: RPCValue = .object(["profile_id": .string("synthetic_restored"), "generation": .number(7), "closing": .bool(false),
                "operations": .array(row.map { [$0] } ?? []), "previous_operations": .array([]), "blockers": .array([])])
            if holdRead {
                holdRead = false; readEntered = true
                await withCheckedContinuation { held = $0 }
                if failRead { throw RPCFailure.disconnected }
            }
            return snapshot
        case "maintenance.pending":
            pendingCalls += 1
            return .object(["profile_id": .string("synthetic_restored"), "generation": .number(7),
                            "pending": .array(includeTicket ? [try ChannelReconciliationFixture.ticket()] : [])])
        case "maintenance.decide":
            decisions += 1; includeTicket = false; row = try ChannelReconciliationFixture.operation("denied")
            if holdDecision {
                holdDecision = false; decisionEntered = true
                await withCheckedContinuation { heldDecision = $0 }
            }
            return .object(["accepted": .bool(true)])
        case "maintenance.cleanup": row = try custody(false); return row!
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class ChannelReconciliationLifecycleTests: XCTestCase {
    @MainActor
    private func prepared(_ wire: ReconciliationLifecycleWire) async throws -> ChannelReconciliationStore {
        let store = ChannelReconciliationStore(maxPolls: 0)
        store.configure(ChannelReconciliationClient(request: { try await wire.request($0, $1) }))
        await store.prepare(chatID: 42, revision: 3, enabled: true)
        await store.submit(); await store.refresh()
        XCTAssertTrue(store.canDecide)
        return store
    }
    private func wait(_ condition: @escaping @Sendable () async -> Bool) async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while !(await condition()), ContinuousClock.now < deadline { await Task.yield() }
        guard await condition() else { throw RPCFailure.unavailable }
    }
    @MainActor
    func testLateReadAndLateReadFailureCannotResurrectOrEraseANewerOwnerDecision() async throws {
        for fail in [false, true] {
            let wire = ReconciliationLifecycleWire(), store = try await prepared(wire)
            let callsBefore = await wire.counts()
            await wire.armRead(fail: fail)
            let old = Task { await store.refresh() }
            try await wait { await wire.enteredRead() }
            XCTAssertTrue(store.canDecide)
            await store.decide(allow: false)
            await wire.releaseRead(); await old.value
            XCTAssertNil(store.approval)
            XCTAssertFalse(store.responseUnconfirmed)
            let callsAfter = await wire.counts()
            XCTAssertEqual(callsAfter.0, callsBefore.0, "The retired status must not start another pending read")
            XCTAssertEqual(callsAfter.1, 1)
            await store.refresh()
            XCTAssertEqual(store.operation?.phase, "denied")
            XCTAssertFalse(store.canDecide)
            await store.close()
        }
    }
    @MainActor
    func testConfigureRetiresAnOldReviewWithoutChangingTheSuccessorRequestState() async throws {
        let first = ReconciliationLifecycleWire(), second = ReconciliationLifecycleWire(
            reviewChanges: ["workspace_path": .string("/private/tmp/new-selected-workspace")])
        let store = ChannelReconciliationStore(maxPolls: 0)
        store.configure(ChannelReconciliationClient(request: { try await first.request($0, $1) }))
        await first.armReview()
        let old = Task { await store.prepare(chatID: 42, revision: 3, enabled: true) }
        try await wait { await first.enteredReview() }
        store.configure(ChannelReconciliationClient(request: { try await second.request($0, $1) }))
        XCTAssertFalse(store.isRequesting)
        await second.armReview()
        let next = Task { await store.prepare(chatID: 42, revision: 3, enabled: true) }
        try await wait { await second.enteredReview() }
        await first.releaseReview(); await old.value
        XCTAssertTrue(store.isRequesting)
        XCTAssertNil(store.review)
        await second.releaseReview(); await next.value
        XCTAssertEqual(store.review?.workspacePath, "/private/tmp/new-selected-workspace")
        XCTAssertFalse(store.isRequesting)
        await store.close()
    }
    @MainActor
    func testClosingDuringADecisionCannotDeliverItsLateAcknowledgementIntoTheNextContext() async throws {
        let first = ReconciliationLifecycleWire(), store = try await prepared(first)
        await first.armDecision()
        let decision = Task { await store.decide(allow: false) }
        try await wait { await first.enteredDecision() }
        await store.close()
        let second = ReconciliationLifecycleWire()
        store.configure(ChannelReconciliationClient(request: { try await second.request($0, $1) }))
        await store.prepare(chatID: 42, revision: 3, enabled: true)
        let nextID = store.review?.id
        await first.releaseDecision(); await decision.value
        XCTAssertEqual(store.review?.id, nextID)
        XCTAssertNil(store.operation)
        XCTAssertNil(store.approval)
        XCTAssertNil(store.errorMessage)
        XCTAssertFalse(store.isRequesting)
        XCTAssertFalse(store.responseUnconfirmed)
        let calls = await second.counts()
        XCTAssertEqual(calls.0, 0)
        XCTAssertEqual(calls.1, 0)
        await store.close()
    }
    @MainActor
    func testHistoricalUnknownCleanupFlagSurvivesAfterTheActualOwnerHasRetired() async throws {
        let wire = ReconciliationLifecycleWire(), store = try await prepared(wire)
        try await wire.losePublicationWithCustody(); await store.refresh()
        XCTAssertEqual(store.operation?.phase, "cleanup_pending")
        XCTAssertTrue(store.operation?.cleanupOwnerPending == true)
        XCTAssertTrue(store.blocksInteraction)
        XCTAssertFalse(store.canDecide)
        await wire.armRead(fail: false)
        let old = Task { await store.refresh() }
        try await wait { await wire.enteredRead() }
        await store.cleanup()
        await wire.releaseRead(); await old.value
        XCTAssertEqual(store.operation?.phase, "publication_unknown")
        XCTAssertEqual(store.operation?.channelReceipt?.status, "unknown")
        XCTAssertFalse(store.operation?.cleanupOwnerPending == true)
        XCTAssertFalse(store.operation?.resourcesPending == true)
        XCTAssertFalse(store.blocksInteraction)
        XCTAssertTrue(store.mayPrepare)
        XCTAssertFalse(store.canDecide)
        await store.close()
    }
    func testOnlyUnknownReceiptsCanRetainHistoricalCleanupEvidence() throws {
        for status in ["failed", "cancelled", "completed"] {
            var changes: [String: RPCValue] = ["cleanup_pending": .bool(true)]
            if status != "completed" {
                changes["effect_status"] = .string("no_route_change_confirmed")
                changes["route_revision_after"] = .null
                changes["configuration_hash_after"] = .null
            }
            XCTAssertThrowsError(try ChannelReconciliationReceipt(
                ChannelReconciliationFixture.receipt(status: status, changes: changes),
                operationID: ChannelReconciliationFixture.operationID))
        }
        XCTAssertNoThrow(try ChannelReconciliationReceipt(ChannelReconciliationFixture.receipt(
            status: "unknown", changes: ["cleanup_pending": .bool(true)]),
            operationID: ChannelReconciliationFixture.operationID))
    }
}
