import Foundation
import XCTest
@testable import DesktopCore

private enum AuthorityFlow: Equatable {
    case maintenance, recovery, memory, profile
    var kind: String {
        switch self {
        case .maintenance: return "backup.create"
        case .recovery: return "workspace.backup.create"
        case .memory: return "memory.restore.copy_scope"
        case .profile: return "profile.restore.activate"
        }
    }
    var tab: String {
        switch self {
        case .maintenance: return "maintenance"
        case .recovery: return "recovery"
        case .memory: return "memory-restored"
        case .profile: return "restored"
        }
    }
    func review() throws -> RPCValue {
        switch self {
        case .maintenance: return MaintenanceFixtures.review
        case .recovery: return try RecoveryFixture.review()
        case .memory: return try RestoredMemoryFixture.review()
        case .profile: return try RestoreFixture.reviewValue()
        }
    }
    func ticket() throws -> RPCValue {
        switch self {
        case .maintenance: return MaintenanceFixtures.approval
        case .recovery: return try RecoveryFixture.approval()
        case .memory: return try RestoredMemoryFixture.approval()
        case .profile:
            let review = try review(), operation = try operation()
            let args = try MaintenanceValidation.canonical(.object(["review_id": review["review_id"], "review_digest": review["review_digest"], "review": review["metadata"]]))
            let binding: RPCValue = .object(["domain": .string("jarvis.host.approval/1"), "profile": review["metadata"]["profile_id"], "generation": review["metadata"]["generation"], "purpose": .string(kind), "operation": operation["operation_id"], "arguments_json": .string(args)])
            return .object(["approval_id": .string(String(repeating: "9", count: 32)), "scope": operation["scope"], "purpose": .string(kind), "operation_id": operation["operation_id"], "arguments_json": .string(args), "digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(binding))), "expires_at_ns": .string("4102444800000000000")])
        }
    }
    func operation(kind: String? = nil, phase: String = "awaiting_owner", result: RPCValue = .null) throws -> RPCValue {
        let review = try review(), selectedKind = kind ?? self.kind, effect = selectedKind == self.kind
        let metadata = review["metadata"]
        return .object(["operation_id": .string(effect && self == .profile ? RestoreFixture.operationID : String(repeating: effect ? "b" : "8", count: 32)), "kind": .string(selectedKind), "phase": .string(phase), "stage": .null, "result": result, "error_code": .null, "receipt": .null, "receipt_durable": .bool(false), "worker_pending": .bool(phase == "working"), "cancellation_requested": .bool(false), "scope": .object(["profile_id": metadata["profile_id"], "generation": metadata["generation"]]), "review_id": effect ? review["review_id"] : .null, "review_digest": effect ? review["review_digest"] : .null, "cleanup_owner_pending": .bool(false)])
    }
}

private actor AuthorityReadGate {
    private var entered = false
    private var released = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func wait() async {
        entered = true
        if released { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func didEnter() -> Bool { entered }
    func release() {
        released = true
        let current = waiters; waiters = []
        current.forEach { $0.resume() }
    }
}

/// Own in-memory transport only. No process, profile, approval or filesystem
/// operation is real; failures interrupt the same status/pending calls as IPC.
private actor AuthorityInterruptionWire {
    let flow: AuthorityFlow
    private var rows: [RPCValue] = []
    private var calls: [String] = []
    private var includesTicket = true
    private var nextFailure: (String, RPCFailure, AuthorityReadGate?)?
    init(_ flow: AuthorityFlow) { self.flow = flow }
    func failNext(_ method: String, error: RPCFailure = .disconnected, gate: AuthorityReadGate? = nil) { nextFailure = (method, error, gate) }
    func showTicket(_ enabled: Bool) { includesTicket = enabled }
    func count(_ method: String) -> Int { calls.filter { $0 == method }.count }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append(method)
        if let failure = nextFailure, failure.0 == method {
            nextFailure = nil
            if let gate = failure.2 { await gate.wait() }
            throw failure.1
        }
        let review = try flow.review(), metadata = review["metadata"]
        switch method {
        case "maintenance.status":
            return .object(["profile_id": metadata["profile_id"], "generation": metadata["generation"], "closing": .bool(false), "operations": .array(rows), "previous_operations": .array([])])
        case "maintenance.pending":
            return .object(["profile_id": metadata["profile_id"], "generation": metadata["generation"], "pending": .array(includesTicket && rows.contains { $0["kind"] == .string(flow.kind) && $0["phase"] == .string("awaiting_owner") } ? [try flow.ticket()] : [])])
        case "backup.review": return review
        case "workspace.backup.review", "memory.restore.review":
            return try flow.operation(kind: method, phase: "complete", result: .object(["review": review]))
        case "memory.restore.inventory":
            return try flow.operation(kind: method, phase: "complete", result: .object(["inventory": RestoredMemoryFixture.inventory()]))
        case "profile.restore.review":
            let row = try flow.operation(kind: method, phase: "complete", result: .object(["review": review])); rows = [row]; return row
        case flow.kind:
            let row = try flow.operation(); rows = [row]; return row
        case "maintenance.decide":
            rows = [try flow.operation(phase: "working")]
            return .object(["accepted": .bool(true)])
        default: throw RPCFailure.configuration
        }
    }
}

@MainActor private struct AuthorityHarness {
    let approval: () -> MaintenanceApproval?
    let canDecide: () -> Bool
    let unconfirmed: () -> Bool
    let operation: () -> MaintenanceOperation?
    let reviewID: () -> String?
    let notice: () -> OwnerDecisionNotice?
    let error: () -> String?
    let refresh: () async -> Void
    let decide: (MaintenanceApproval) async -> Void
    let close: () async -> Void
}

final class OwnerAuthorityInterruptionTests: XCTestCase {
    @MainActor private func ready(_ flow: AuthorityFlow, wire: AuthorityInterruptionWire) async throws -> AuthorityHarness {
        let pause: @Sendable () async throws -> Void = { try await Task.sleep(for: .seconds(60)) }
        switch flow {
        case .maintenance:
            let store = MaintenanceStore(maxPolls: 0, pause: pause)
            store.configure(client: MaintenanceClient { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true)
            await store.prepareBackup(parent: "/private/tmp/prueba-Ω", name: "jarvis-copia"); await store.submitReview(); await store.refresh()
            return AuthorityHarness(approval: { store.approval }, canDecide: { store.canDecide }, unconfirmed: { store.responseUnconfirmed }, operation: { store.operation }, reviewID: { store.review?.id }, notice: { OwnerDecisionNotice(tab: flow.tab, operation: store.operation, reviewID: store.review?.id, expiresAt: store.review?.expiresAt, unconfirmed: store.responseUnconfirmed) }, error: { store.errorMessage }, refresh: { await store.refresh() }, decide: { _ in await store.decide(allow: true) }, close: { await store.close() })
        case .recovery:
            let runtime = ProfileRuntimeGuard()
            let store = RecoveryExtensionStore(runtime: runtime, maxPolls: 1, pause: pause)
            store.configure(RecoveryExtensionClient(runtime: runtime) { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true)
            await store.prepareWorkspaceBackup(entries: [try RecoveryWorkspaceEntry(path: "selected.txt")], parent: "/private/tmp/fixture-parent", name: "portable"); await store.submit(); await store.refresh()
            return AuthorityHarness(approval: { store.approval }, canDecide: { store.canDecide }, unconfirmed: { store.responseUnconfirmed }, operation: { store.operation }, reviewID: { store.effectReview?.id }, notice: { OwnerDecisionNotice(tab: flow.tab, operation: store.operation, reviewID: store.effectReview?.id, expiresAt: store.effectReview?.expiresAt, unconfirmed: store.responseUnconfirmed) }, error: { store.errorMessage }, refresh: { await store.refresh() }, decide: { await store.decide(allow: true, ticket: $0) }, close: { await store.close() })
        case .memory:
            let runtime = ProfileRuntimeGuard()
            let store = RestoredMemoryStore(runtime: runtime, maxPolls: 1, pause: pause)
            store.configure(RestoredMemoryClient(runtime: runtime) { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true); store.selectAgent("jarvis")
            await store.loadInventory(); store.selectSource(RestoredMemoryFixture.sourceID); await store.prepareReview(); await store.submit(); await store.refresh()
            return AuthorityHarness(approval: { store.approval }, canDecide: { store.canDecide }, unconfirmed: { store.responseUnconfirmed }, operation: { store.operation }, reviewID: { store.effectReview?.id }, notice: { OwnerDecisionNotice(tab: flow.tab, operation: store.operation, reviewID: store.effectReview?.id, expiresAt: store.effectReview?.expiresAt, unconfirmed: store.responseUnconfirmed) }, error: { store.errorMessage }, refresh: { await store.refresh() }, decide: { await store.decide(allow: true, ticket: $0) }, close: { await store.close() })
        case .profile:
            let store = RestoreProfileStore(maxPolls: 0, pause: pause)
            store.configure(RestoreProfileClient { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true)
            await store.review(path: "/private/tmp/restored-fixture"); await store.refresh(); await store.submit(); await store.refresh()
            return AuthorityHarness(approval: { store.approval }, canDecide: { store.canDecide }, unconfirmed: { store.responseUnconfirmed }, operation: { store.operation }, reviewID: { store.review?.id }, notice: { OwnerDecisionNotice(tab: flow.tab, operation: store.operation, reviewID: store.review?.id, expiresAt: store.review?.expiresAt, unconfirmed: store.responseUnconfirmed) }, error: { store.errorMessage }, refresh: { await store.refresh() }, decide: { _ in await store.decide(allow: true) }, close: { await store.close() })
        }
    }

    @MainActor private func checkFailureAndRecovery(_ flow: AuthorityFlow) async throws {
        for method in ["maintenance.status", "maintenance.pending"] {
            let wire = AuthorityInterruptionWire(flow), store = try await ready(flow, wire: wire)
            let ticket = try XCTUnwrap(store.approval()), original = try XCTUnwrap(store.operation()), review = store.reviewID()
            XCTAssertTrue(store.canDecide())
            await wire.failNext(method); await store.refresh()
            XCTAssertNil(store.approval(), method); XCTAssertFalse(store.canDecide(), "The view's final approval condition is false after authority observation fails")
            XCTAssertTrue(store.unconfirmed()); XCTAssertEqual(store.operation()?.id, original.id); XCTAssertEqual(store.reviewID(), review)
            let notice = try XCTUnwrap(store.notice())
            XCTAssertTrue(notice.unconfirmed); XCTAssertFalse(notice.awaitingDecision)
            XCTAssertTrue(notice.title(at: Date()).contains("sin confirmar")); XCTAssertNotNil(store.error())
            await store.decide(ticket)
            let deniedCount = await wire.count("maintenance.decide"); XCTAssertEqual(deniedCount, 0)
            await wire.showTicket(false); await store.refresh()
            XCTAssertNil(store.approval()); XCTAssertFalse(store.canDecide(), "A status read cannot recover a ticket that pending did not return")
            await wire.showTicket(true); await store.refresh()
            XCTAssertFalse(store.unconfirmed()); XCTAssertTrue(store.canDecide())
            XCTAssertEqual(store.approval()?.params, ticket.params); XCTAssertEqual(store.approval()?.reviewMetadata, ticket.reviewMetadata)
            XCTAssertEqual(store.operation()?.id, original.id); XCTAssertEqual(store.reviewID(), review)
            let submits = await wire.count(flow.kind), decisions = await wire.count("maintenance.decide")
            XCTAssertEqual(submits, 1); XCTAssertEqual(decisions, 0, "Refreshing restores only observed authority, never resends a decision or effect")
            await store.close()
        }
    }

    @MainActor private func checkStaleReadErrorAfterDecision(_ flow: AuthorityFlow) async throws {
        let wire = AuthorityInterruptionWire(flow), store = try await ready(flow, wire: wire)
        let ticket = try XCTUnwrap(store.approval()), gate = AuthorityReadGate()
        await wire.failNext("maintenance.pending", gate: gate)
        let oldRead = Task { await store.refresh() }
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while !(await gate.didEnter()), ContinuousClock.now < deadline { await Task.yield() }
        let entered = await gate.didEnter(); XCTAssertTrue(entered)
        await store.decide(ticket)
        let sent = await wire.count("maintenance.decide"); XCTAssertEqual(sent, 1)
        await gate.release(); await oldRead.value
        XCTAssertFalse(store.unconfirmed(), "An error from before the owner's newer decision cannot invalidate its current observation sequence")
        XCTAssertNil(store.error()); XCTAssertNil(store.approval())
        await store.refresh()
        XCTAssertEqual(store.operation()?.phase, "working"); XCTAssertFalse(store.canDecide())
        let submits = await wire.count(flow.kind), decisions = await wire.count("maintenance.decide")
        XCTAssertEqual(submits, 1); XCTAssertEqual(decisions, 1)
        await store.close()
    }

    @MainActor func testMaintenanceReadFailureWithdrawsTicketAndExactRefreshRecoversWithoutReplay() async throws { try await checkFailureAndRecovery(.maintenance) }
    @MainActor func testRecoveryReadFailureWithdrawsTicketAndExactRefreshRecoversWithoutReplay() async throws { try await checkFailureAndRecovery(.recovery) }
    @MainActor func testMemoryReadFailureWithdrawsTicketAndExactRefreshRecoversWithoutReplay() async throws { try await checkFailureAndRecovery(.memory) }
    @MainActor func testProfileReadFailureWithdrawsTicketAndExactRefreshRecoversWithoutReplay() async throws { try await checkFailureAndRecovery(.profile) }
    @MainActor func testMaintenanceLateReadErrorCannotInvalidateNewerOwnerDecision() async throws { try await checkStaleReadErrorAfterDecision(.maintenance) }
    @MainActor func testRecoveryLateReadErrorCannotInvalidateNewerOwnerDecision() async throws { try await checkStaleReadErrorAfterDecision(.recovery) }
    @MainActor func testMemoryLateReadErrorCannotInvalidateNewerOwnerDecision() async throws { try await checkStaleReadErrorAfterDecision(.memory) }
    @MainActor func testProfileLateReadErrorCannotInvalidateNewerOwnerDecision() async throws { try await checkStaleReadErrorAfterDecision(.profile) }
}
