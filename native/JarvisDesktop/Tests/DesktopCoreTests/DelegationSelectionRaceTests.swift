import Foundation
import XCTest
@testable import DesktopCore

private enum GoalSelectionFixture {
    static let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
    static func goal(_ digit: String) -> RPCValue {
        let child: RPCValue = .object([
            "index": .number(0), "agent_id": .string("jarvis"), "agent_revision": .number(1),
            "session_id": .string(String(repeating: "b", count: 31) + digit),
            "run_id": .string(String(repeating: "c", count: 31) + digit), "max_steps": .number(1),
            "status": .string("completed"), "phase": .string("settled"), "outcome": .string("known"),
            "answer": .string("Reporte " + digit), "answer_source": .string("model"),
            "answer_bytes": .number(Double(("Reporte " + digit).utf8.count)),
            "answer_sha256": .string(ManagementValidation.digest("Reporte " + digit)),
            "answer_truncated": .bool(false), "action_receipts": .array([]),
            "action_receipts_total": .number(0), "action_receipts_truncated": .bool(false),
            "unknown_effects": .bool(false), "settled": .bool(true)])
        return .object(["goal_id": .string(String(repeating: digit, count: 32)),
            "parent_run_id": .string(String(repeating: digit, count: 32)),
            "parent_session_id": .string(scope.sessionID), "temporary": .bool(false),
            "objective": .string("Objetivo " + digit), "status": .string("completed"),
            "max_steps": .number(2), "reserved_steps": .number(1), "timeout_seconds": .number(30),
            "children": .array([child]), "parent_settled": .bool(true),
            "parent_outcome": .string("known"), "settled": .bool(true),
            "error_code": .null, "permits_replay": .bool(false), "snapshot_truncated": .bool(false)])
    }
}

private actor SelectionReadGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var entered = false
    func hold() async { entered = true; await withCheckedContinuation { continuation = $0 } }
    func waitUntilHeld() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while !entered {
            guard ContinuousClock.now < deadline else { throw RPCFailure.unavailable }
            await Task.yield()
        }
    }
    func release() { continuation?.resume(); continuation = nil }
}

private actor SelectionWire {
    private var rows = [GoalSelectionFixture.goal("1"), GoalSelectionFixture.goal("2")]
    private var gate: SelectionReadGate?
    private var methods: [String] = []
    private var gets: [String] = []
    func holdNextGet(_ gate: SelectionReadGate) { self.gate = gate }
    func replaceCatalogue(_ rows: [RPCValue]) { self.rows = rows }
    func requests() -> ([String], [String]) { (methods, gets) }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        methods.append(method)
        switch method {
        case "delegations.list":
            return .object(["goals": .array(rows), "has_more": .bool(false), "next_offset": .number(Double(rows.count))])
        case "delegations.get":
            guard let id = params["goal_id"]?.string,
                  let row = rows.first(where: { $0["goal_id"].string == id }) else { throw RPCFailure.invalidResponse }
            gets.append(id)
            if let gate { self.gate = nil; await gate.hold() }
            return .object(["goal": row])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class DelegationSelectionRaceTests: XCTestCase {
    @MainActor func testSelectingAnotherSettledGoalInvalidatesTheOlderInFlightRead() async throws {
        let wire = SelectionWire(), gate = SelectionReadGate(), store = DelegationStore()
        store.configure(DelegationClient(request: { try await wire.request($0, $1) }))
        store.setScope(GoalSelectionFixture.scope); await store.reloadCatalogue()
        let next = try XCTUnwrap(store.goals.last)
        XCTAssertEqual(store.goal?.id, String(repeating: "1", count: 32))
        await wire.holdNextGet(gate)
        let oldRead = Task { @MainActor in await store.refresh() }
        try await gate.waitUntilHeld()
        let selecting = Task { @MainActor in await store.select(next) }
        for _ in 0..<20 where store.goal?.id != next.id { await Task.yield() }
        XCTAssertEqual(store.goal?.id, next.id)
        await gate.release(); await oldRead.value; await selecting.value
        XCTAssertEqual(store.goal?.id, next.id)
        let (methods, gets) = await wire.requests()
        XCTAssertEqual(gets.last, next.id)
        XCTAssertTrue(methods.allSatisfy { ["delegations.list", "delegations.get"].contains($0) })
        await store.close()
    }

    @MainActor func testReloadCatalogueDiscoversNewGoalsWithoutChangingTheCurrentSelection() async throws {
        let wire = SelectionWire(), store = DelegationStore()
        store.configure(DelegationClient(request: { try await wire.request($0, $1) }))
        store.setScope(GoalSelectionFixture.scope); await store.reloadCatalogue()
        let selected = try XCTUnwrap(store.goal?.id)
        await wire.replaceCatalogue([GoalSelectionFixture.goal("3"), GoalSelectionFixture.goal("1"), GoalSelectionFixture.goal("2")])
        await store.reloadCatalogue()
        XCTAssertEqual(store.goal?.id, selected)
        XCTAssertEqual(store.goals.first?.id, String(repeating: "3", count: 32))
        XCTAssertEqual(store.goals.count, 3)
        let (methods, _) = await wire.requests()
        XCTAssertEqual(methods, ["delegations.list", "delegations.list"])
        await store.close()
    }
}
