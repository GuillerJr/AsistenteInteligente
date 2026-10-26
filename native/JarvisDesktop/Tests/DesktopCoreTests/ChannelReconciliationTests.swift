import Foundation
import XCTest
@testable import DesktopCore

enum ChannelReconciliationFixture {
    static let operationID = String(repeating: "9", count: 32)
    static func review(_ changes: [String: RPCValue] = [:]) throws -> RPCValue {
        let hash = String(repeating: "e", count: 64)
        var row: [String: RPCValue] = ["profile_id": .string("synthetic_restored"), "generation": .number(7), "bot_id": .number(424242), "chat_id": .number(42), "agent_id": .string("jarvis"), "agent_revision": .number(1), "session_id": .string(String(repeating: "a", count: 32)), "route_revision": .number(3), "enabled_before": .bool(false), "enabled_after": .bool(true), "workspace_id": .string(hash), "workspace_path": .string("/private/tmp/workspace-sintetico"), "configuration_hash": .string(hash), "marker_sha256": .string(hash), "admission_sha256": .string(hash), "network_started": .bool(false), "provider_started": .bool(false), "grants_restored": .bool(false), "historical_events_replayed": .bool(false), "external_bot_verified": .bool(false)]
        row.merge(changes) { _, new in new }; let metadata = RPCValue.object(row)
        return .object(["review_id": .string(String(repeating: "c", count: 32)), "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata))), "expires_in_seconds": .number(120), "metadata": metadata])
    }
    static func operation(_ phase: String = "awaiting_owner", receipt: RPCValue = .null) throws -> RPCValue {
        let value = try ChannelReconciliationReview(review())
        return .object(["operation_id": .string(operationID), "kind": .string("telegram.restore.reconcile"), "phase": .string(phase), "stage": .null, "result": .null, "error_code": .null, "receipt": receipt, "receipt_durable": .bool(receipt != .null), "worker_pending": .bool(false), "cancellation_requested": .bool(false), "scope": RestoredMemoryFixture.scope(), "review_id": .string(value.id), "review_digest": .string(value.digest), "cleanup_owner_pending": .bool(false)])
    }
    static func ticket() throws -> RPCValue {
        let review = try ChannelReconciliationReview(review())
        let args = try MaintenanceValidation.canonical(.object(["review_id": .string(review.id), "review_digest": .string(review.digest), "review": review.metadata]))
        let identity: RPCValue = .object(["domain": .string("jarvis.host.approval/1"), "profile": .string(review.scope.profileID), "generation": .number(7), "purpose": .string("telegram.restore.reconcile"), "operation": .string(operationID), "arguments_json": .string(args)])
        return .object(["approval_id": .string(String(repeating: "1", count: 32)), "scope": RestoredMemoryFixture.scope(), "purpose": .string("telegram.restore.reconcile"), "operation_id": .string(operationID), "arguments_json": .string(args), "digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(identity))), "expires_at_ns": .string("4102444800000000000")])
    }
    static func receipt(status: String = "completed", changes: [String: RPCValue] = [:]) throws -> RPCValue {
        let review = try ChannelReconciliationReview(review()), success = status == "completed"
        var row: [String: RPCValue] = ["operation_id": .string(operationID), "purpose": .string("telegram.restore.reconcile"), "request_digest": .string(review.digest), "status": .string(status), "effect_status": .string(success ? "route_reconciled" : "route_change_unknown"), "bot_id": .number(424242), "chat_id": .number(42), "route_revision_before": .number(3), "route_revision_after": success ? .number(4) : .null, "enabled_before": .bool(false), "enabled_after": .bool(true), "configuration_hash_before": .string(RestoredMemoryFixture.hash), "configuration_hash_after": success ? .string(String(repeating: "f", count: 64)) : .null, "error_code": .null, "cleanup_pending": .bool(false), "network_started": .bool(false), "provider_started": .bool(false), "grants_restored": .bool(false), "historical_events_replayed": .bool(false), "permits_replay": .bool(false)]
        row.merge(changes) { _, new in new }; return .object(row)
    }
}

private actor ChannelWire {
    var row: RPCValue?
    var lostSubmit = false
    var lostStatus = false
    var decisions = 0
    var submits = 0
    var includeTicket = true
    func loseStatus() { lostStatus = true }
    func loseSubmit() { lostSubmit = true }
    func snapshot() -> (Int, Int) { (submits, decisions) }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        switch method {
        case "telegram.restore.review": return try ChannelReconciliationFixture.review()
        case "telegram.restore.reconcile":
            submits += 1; row = try ChannelReconciliationFixture.operation()
            if lostSubmit { throw RPCFailure.disconnected }; return row!
        case "maintenance.status":
            if lostStatus { throw RPCFailure.disconnected }
            return .object(["profile_id": .string("synthetic_restored"), "generation": .number(7), "closing": .bool(false), "operations": .array(row.map { [$0] } ?? []), "previous_operations": .array([]), "blockers": .array([])])
        case "maintenance.pending": return .object(["profile_id": .string("synthetic_restored"), "generation": .number(7), "pending": .array(includeTicket ? [try ChannelReconciliationFixture.ticket()] : [])])
        case "maintenance.decide":
            decisions += 1; includeTicket = false; row = try ChannelReconciliationFixture.operation("denied")
            return .object(["accepted": .bool(true)])
        case "maintenance.cancel": row = try ChannelReconciliationFixture.operation("cancelled"); return row!
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class ChannelReconciliationTests: XCTestCase {
    func testReviewBindsExactArgumentsAndDoesNotGrantNetworkOrReplay() throws {
        let review = try ChannelReconciliationReview(ChannelReconciliationFixture.review())
        let operation = try MaintenanceOperation(ChannelReconciliationFixture.operation())
        let ticket = try MaintenanceApproval(ChannelReconciliationFixture.ticket())
        XCTAssertTrue(review.matches(operation)); XCTAssertTrue(review.matches(ticket, operation: operation))
        for key in ["network_started", "provider_started", "grants_restored", "historical_events_replayed", "external_bot_verified"] {
            XCTAssertThrowsError(try ChannelReconciliationReview(ChannelReconciliationFixture.review([key: .bool(true)])))
        }
        XCTAssertThrowsError(try ChannelReconciliationReview(ChannelReconciliationFixture.review(["enabled_after": .bool(false)])))
        XCTAssertFalse(review.matches(ticket, operation: operation, now: .distantFuture))
    }
    func testCompletedReceiptRequiresRevisionAndDigestAndSeparateHistoricalUncertainty() throws {
        let complete = try MaintenanceOperation(ChannelReconciliationFixture.operation("complete", receipt: ChannelReconciliationFixture.receipt()))
        XCTAssertTrue(complete.success)
        let unknown = try MaintenanceOperation(ChannelReconciliationFixture.operation("publication_unknown", receipt: ChannelReconciliationFixture.receipt(status: "unknown")))
        XCTAssertFalse(unknown.success); XCTAssertTrue(unknown.settled)
        for changes: [String: RPCValue] in [["route_revision_after": .number(8)], ["permits_replay": .bool(true)], ["request_digest": .string(String(repeating: "0", count: 64))]] {
            XCTAssertThrowsError(try MaintenanceOperation(ChannelReconciliationFixture.operation("complete", receipt: ChannelReconciliationFixture.receipt(changes: changes))))
        }
    }
    @MainActor func testLostStatusWithdrawsTicketAndRetainsExactOperation() async throws {
        let wire = ChannelWire(), store = ChannelReconciliationStore(maxPolls: 0)
        store.configure(ChannelReconciliationClient(request: { try await wire.request($0, $1) }))
        await store.prepare(chatID: 42, revision: 3, enabled: true); await store.submit(); await store.refresh()
        XCTAssertTrue(store.canDecide)
        await wire.loseStatus(); await store.refresh()
        XCTAssertFalse(store.canDecide); XCTAssertNil(store.approval); XCTAssertTrue(store.responseUnconfirmed)
        XCTAssertEqual(store.operation?.id, ChannelReconciliationFixture.operationID)
        await store.decide(allow: true)
        let calls = await wire.snapshot(); XCTAssertEqual(calls.0, 1); XCTAssertEqual(calls.1, 0)
        await store.close()
    }
    @MainActor func testSubmissionWithLostAcknowledgmentIsFoundWithoutResubmission() async throws {
        let wire = ChannelWire(), store = ChannelReconciliationStore(maxPolls: 0)
        store.configure(ChannelReconciliationClient(request: { try await wire.request($0, $1) }))
        await store.prepare(chatID: 42, revision: 3, enabled: true); await wire.loseSubmit(); await store.submit()
        XCTAssertTrue(store.responseUnconfirmed); await store.submit()
        await store.refresh(); XCTAssertFalse(store.responseUnconfirmed); XCTAssertTrue(store.canDecide)
        let calls = await wire.snapshot(); XCTAssertEqual(calls.0, 1)
        await store.close()
    }
    @MainActor func testDecisionConsumedOnceAndNavigationRemainsBoundToChannels() async throws {
        let wire = ChannelWire(), store = ChannelReconciliationStore(maxPolls: 0)
        store.configure(ChannelReconciliationClient(request: { try await wire.request($0, $1) }))
        await store.prepare(chatID: 42, revision: 3, enabled: true); await store.submit(); await store.refresh()
        async let first: Void = store.decide(allow: false)
        async let second: Void = store.decide(allow: false)
        _ = await (first, second); await store.refresh()
        let calls = await wire.snapshot(); XCTAssertEqual(calls.1, 1)
        let notice = try XCTUnwrap(OwnerDecisionNotice(tab: "channels-restored", operation: store.operation, reviewID: store.review?.id, expiresAt: store.review?.expiresAt))
        let navigation = SettingsOwnerNavigation(); navigation.configure(runtimeID: UUID()); navigation.reveal(notice)
        XCTAssertEqual(navigation.selectedTab, "channels-restored"); XCTAssertEqual(notice.phase, "denied")
        await store.close()
    }
}
