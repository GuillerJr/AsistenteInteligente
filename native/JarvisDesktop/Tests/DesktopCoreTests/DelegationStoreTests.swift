import Foundation
import XCTest
@testable import DesktopCore

private enum DelegationFixture {
    static let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
    static let childID = String(repeating: "b", count: 32)
    static let childRun = String(repeating: "c", count: 32)
    static func snapshot(id: String, completed: Bool = false, childChanges: [String: RPCValue] = [:], changes: [String: RPCValue] = [:]) -> RPCValue {
        var child: [String: RPCValue] = ["index": .number(0), "agent_id": .string("jarvis"), "agent_revision": .number(1),
            "session_id": .string(childID), "run_id": .string(childRun), "max_steps": .number(10),
            "status": .string(completed ? "completed" : "waiting_approval"),
            "phase": .string(completed ? "settled" : "waiting_approval"), "outcome": .string(completed ? "known" : "pending"),
            "answer": completed ? .string("Reporte sintético") : .null, "answer_source": completed ? .string("model") : .null,
            "answer_bytes": .number(completed ? Double("Reporte sintético".utf8.count) : 0),
            "answer_sha256": completed ? .string(ManagementValidation.digest("Reporte sintético")) : .null,
            "answer_truncated": .bool(false), "action_receipts": .array([]), "action_receipts_total": .number(0),
            "action_receipts_truncated": .bool(false), "unknown_effects": .bool(false), "settled": .bool(completed)]
        child.merge(childChanges) { _, new in new }
        var goal: [String: RPCValue] = ["goal_id": .string(id), "parent_run_id": .string(id),
            "parent_session_id": .string(scope.sessionID), "temporary": .bool(false), "objective": .string("Objetivo sintético"),
            "status": .string(completed ? "completed" : "running"), "max_steps": .number(20), "reserved_steps": .number(10),
            "timeout_seconds": .number(120), "children": .array([.object(child)]), "created_ns": .number(1), "error_code": .null,
            "parent_settled": .bool(completed), "parent_outcome": .string(completed ? "known" : "pending"),
            "settled": .bool(completed), "snapshot_truncated": .bool(false), "permits_replay": .bool(false)]
        goal.merge(changes) { _, new in new }; return .object(goal)
    }
    static var approval: RPCValue {
        .object(["approval_id": .string(String(repeating: "e", count: 32)), "session_id": .string(childID), "temporary": .bool(false),
            "run_id": .string(childRun), "tool_name": .string("workspace_write"),
            "arguments_json": .string("{\"path\":\"fixture.txt\",\"text\":\"SINTETICO\"}"),
            "digest": .string(String(repeating: "f", count: 64)), "expires_at": .number(Date().timeIntervalSince1970 + 60)])
    }
}

private actor DelegationReadGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var entered = false
    func hold() async { entered = true; await withCheckedContinuation { continuation = $0 } }
    func waitUntilHeld() async { while !entered { await Task.yield() } }
    func release() { continuation?.resume(); continuation = nil }
}

private actor DelegationWire {
    var id = String(repeating: "d", count: 32)
    var loseStart = false
    var allowed = false
    var gate: DelegationReadGate?
    private var calls: [(String, [String: RPCValue])] = []
    func setLostStart() { loseStart = true }
    func setGate(_ value: DelegationReadGate) { gate = value }
    func count(_ method: String) -> Int { calls.filter { $0.0 == method }.count }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append((method, params))
        switch method {
        case "delegations.list":
            return .object(["goals": .array([DelegationFixture.snapshot(id: id)]), "has_more": .bool(false), "next_offset": .number(1)])
        case "delegations.get":
            let prior = DelegationFixture.snapshot(id: id)
            if let gate { self.gate = nil; await gate.hold() }
            return .object(["goal": prior])
        case "delegations.start":
            id = params["request_id"]!.string!
            if loseStart { throw RPCFailure.disconnected }
            return .object(["goal": DelegationFixture.snapshot(id: id)])
        case "delegations.lookup":
            XCTAssertEqual(params["request_id"], .string(id))
            return .object(["goal": DelegationFixture.snapshot(id: id), "permits_replay": .bool(false), "historical_ack_established": .bool(false)])
        case "approvals.list":
            XCTAssertEqual(params["session_id"], .string(DelegationFixture.childID))
            XCTAssertEqual(params["run_id"], .string(DelegationFixture.childRun))
            return .object(["approvals": .array(allowed ? [] : [DelegationFixture.approval])])
        case "approvals.decide":
            XCTAssertEqual(params["session_id"], .string(DelegationFixture.childID))
            allowed = true; return .object(["accepted": .bool(true)])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class DelegationStoreTests: XCTestCase {
    func testSnapshotsBindParentsChildrenBudgetsAndResultProvenance() throws {
        let id = String(repeating: "d", count: 32)
        let goal = try DelegationSnapshot(DelegationFixture.snapshot(id: id), expectedScope: DelegationFixture.scope)
        XCTAssertFalse(goal.settled)
        XCTAssertThrowsError(try DelegationSnapshot(DelegationFixture.snapshot(id: id,
            childChanges: ["session_id": .string(DelegationFixture.scope.sessionID)]), expectedScope: DelegationFixture.scope))
        XCTAssertThrowsError(try DelegationSnapshot(DelegationFixture.snapshot(id: id, completed: true,
            childChanges: ["answer_source": .string("independently_verified")]), expectedScope: DelegationFixture.scope))
        XCTAssertThrowsError(try DelegationSnapshot(DelegationFixture.snapshot(id: id, completed: true,
            childChanges: ["answer_sha256": .string(String(repeating: "0", count: 64))]), expectedScope: DelegationFixture.scope))
        XCTAssertThrowsError(try DelegationSnapshot(DelegationFixture.snapshot(id: id, changes: ["reserved_steps": .number(21)]), expectedScope: DelegationFixture.scope))
        let complete = try DelegationSnapshot(DelegationFixture.snapshot(id: id, completed: true), expectedScope: DelegationFixture.scope)
        XCTAssertTrue(complete.settled); XCTAssertTrue(complete.follows(goal)); XCTAssertFalse(goal.follows(complete))
    }

    @MainActor func testLostStartOnlyLooksUpTheOriginalGoalIdentity() async throws {
        let wire = DelegationWire(), store = DelegationStore()
        store.configure(DelegationClient(request: { try await wire.request($0, $1) }))
        store.setScope(DelegationFixture.scope); await wire.setLostStart()
        await store.start(objective: "Objetivo sintético", children: [DelegatedTaskSpec(agentID: "jarvis", agentRevision: 1,
            message: "Responde sin herramientas")], maxSteps: 20, timeout: 120)
        let id = try XCTUnwrap(store.pendingRequestID)
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertTrue(store.blocksInteraction)
        await store.refresh()
        XCTAssertEqual(store.goal?.id, id); XCTAssertFalse(store.responseUnconfirmed)
        let writes = await wire.count("delegations.start"), reads = await wire.count("delegations.lookup")
        XCTAssertEqual(writes, 1); XCTAssertGreaterThanOrEqual(reads, 1)
        await store.close()
    }

    @MainActor func testOwnerClickDuringReadIsNotDroppedAndOldReadCannotRestoreTicket() async throws {
        let wire = DelegationWire(), store = DelegationStore()
        store.configure(DelegationClient(request: { try await wire.request($0, $1) }))
        store.setScope(DelegationFixture.scope); await store.refresh()
        let operation = try XCTUnwrap(store.approvals.first)
        let gate = DelegationReadGate(); await wire.setGate(gate)
        let reading = Task { await store.refresh() }; await gate.waitUntilHeld()
        XCTAssertTrue(store.isRefreshing)
        await store.decide(operation, allow: true)
        XCTAssertTrue(store.approvals.isEmpty)
        await gate.release(); await reading.value
        XCTAssertTrue(store.approvals.isEmpty)
        let decisions = await wire.count("approvals.decide"); XCTAssertEqual(decisions, 1)
        await store.refresh(); XCTAssertTrue(store.approvals.isEmpty)
        await store.close()
    }
}
