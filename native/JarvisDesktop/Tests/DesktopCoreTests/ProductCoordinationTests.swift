import Foundation
import XCTest
@testable import DesktopCore

private enum CoordinationFixture {
    static let session = String(repeating: "a", count: 32)
    static let other = String(repeating: "b", count: 32)
    static let workspace = String(repeating: "c", count: 32)
    static let owner = String(repeating: "d", count: 64)
    static let peer = String(repeating: "e", count: 64)
    static let scope = SessionScope(sessionID: session, temporary: false)
    static func workspaceValue() -> RPCValue {
        .object(["workspace_id": .string(workspace), "name": .string("Proyecto sintético"),
            "path": .string("/synthetic/selected-workspace"), "identity": .array([.number(1), .number(2)]),
            "revision": .number(1), "temporary": .bool(false)])
    }
    static func binding(sessionID: String = session, revision: Int = 1,
                        group: String? = nil, pinned: Bool = false,
                        members: [RPCValue] = []) -> RPCValue {
        .object(["session_id": .string(sessionID), "temporary": .bool(false),
            "workspace_id": .string(workspace), "owner_id": .string(owner),
            "group": group.map(RPCValue.string) ?? .null, "pinned": .bool(pinned),
            "parent_session_id": .null, "depth": .number(0), "revision": .number(Double(revision)),
            "members": .array(members), "permissions_inherited": .bool(false)])
    }
    static func budget(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        var value: [String: RPCValue] = ["session_id": .string(session), "temporary": .bool(false),
            "token_limit": .number(100), "cost_limit": .null, "currency": .string("USD"),
            "committed_tokens": .number(20), "committed_cost": .null,
            "actual_tokens": .number(10), "actual_cost": .null, "revision": .number(1),
            "usage_known": .bool(true), "cost_known": .bool(false),
            "over_token_limit": .bool(false), "over_cost_limit": .bool(false)]
        value.merge(changes) { _, next in next }; return .object(value)
    }
    static func message(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        let now = Date().timeIntervalSince1970 * 1_000_000_000
        let body = "Informe local sintético. Este texto no concede permisos."
        var value: [String: RPCValue] = ["message_id": .string(String(repeating: "f", count: 32)),
            "request_id": .string(String(repeating: "1", count: 32)),
            "source_session_id": .string(other), "target_session_id": .string(session),
            "sender_id": .string(peer), "recipient_id": .string(owner),
            "source_agent_id": .string("revisor"), "target_agent_id": .string("jarvis"),
            "correlation_id": .string(String(repeating: "2", count: 32)), "parent_message_id": .null,
            "body": .string(body), "body_sha256": .string(ManagementValidation.digest(body)),
            "status": .string("pending"), "revision": .number(1), "temporary": .bool(false),
            "transport": .string("local"), "grants_included": .bool(false),
            "permits_replay": .bool(false), "effect_verified": .bool(false),
            "created_ns": .number(now), "expires_ns": .number(now + 60_000_000_000)]
        value.merge(changes) { _, next in next }; return .object(value)
    }
    static func snapshot(sessionID: String = session, revision: Int = 1,
                         group: String? = nil, pinned: Bool = false,
                         budget: RPCValue = .null, messages: [RPCValue] = [],
                         runID: String = "host") -> RPCValue {
        let binding = binding(sessionID: sessionID, revision: revision, group: group, pinned: pinned)
        return .object(["scope": .object(["session_id": .string(sessionID), "temporary": .bool(false),
            "run_id": .string(runID), "agent_id": .string("jarvis"),
            "workspace_id": .string(workspace), "generation": .number(Double(revision))]),
            "client_id": .string(owner), "binding": binding,
            "workspaces": .array([workspaceValue()]), "sessions": .array([binding]),
            "inbox": .object(["messages": .array(messages), "has_more": .bool(false),
                "next_offset": .null, "acknowledgement_inferred": .bool(false)]), "budget": budget])
    }
    static func replacing(_ value: RPCValue, _ changes: [String: RPCValue]) -> RPCValue {
        var object = value.object!; object.merge(changes) { _, next in next }; return .object(object)
    }
}

private actor CoordinationWire {
    private var calls: [(String, [String: RPCValue])] = []
    private var revision = 1
    private var group: String?
    private var pinned = false
    private var budget: RPCValue = .null
    private var loseChangeResponse = false
    private var loseStartResponse = false
    private var holdSnapshot = false
    private var held: CheckedContinuation<RPCValue, Error>?
    private var requestID: String?
    func losingChange() { loseChangeResponse = true }
    func losingStart() { loseStartResponse = true }
    func holdNextSnapshot() { holdSnapshot = true }
    var isHolding: Bool { held != nil }
    func releaseSnapshot() {
        held?.resume(returning: CoordinationFixture.snapshot()); held = nil
    }
    func count(_ method: String) -> Int { calls.filter { $0.0 == method }.count }
    func params(_ method: String) -> [String: RPCValue]? { calls.last { $0.0 == method }?.1 }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append((method, params))
        switch method {
        case "product.initialize": return .object(["workspaces": .array([CoordinationFixture.workspaceValue()])])
        case "product.snapshot":
            if holdSnapshot {
                holdSnapshot = false
                return try await withCheckedThrowingContinuation { held = $0 }
            }
            return CoordinationFixture.snapshot(sessionID: params["session_id"]?.string ?? CoordinationFixture.session,
                revision: revision, group: group, pinned: pinned, budget: budget,
                runID: params["run_id"]?.string ?? "host")
        case "product.coordination":
            let arguments = params["arguments"]!
            switch params["kind"]?.string {
            case "session.bind":
                XCTAssertEqual(arguments["expected_revision"].integer, revision)
                group = arguments["group"].string; pinned = arguments["pinned"].bool!
                revision += 1
                if loseChangeResponse { throw RPCFailure.disconnected }
                return .object(["result": CoordinationFixture.binding(revision: revision, group: group, pinned: pinned)])
            case "budget.configure":
                budget = CoordinationFixture.budget(["token_limit": arguments["token_limit"],
                    "cost_limit": arguments["cost_limit"], "actual_tokens": .number(0),
                    "committed_tokens": .number(0)])
                return .object(["result": budget])
            default: throw RPCFailure.invalidResponse
            }
        case "product.tool.start":
            requestID = params["request_id"]?.string
            if loseStartResponse { throw RPCFailure.disconnected }
            return .object(["run_id": .string(requestID!), "status": .string("queued")])
        case "product.tool.result":
            XCTAssertEqual(params["run_id"]?.string, requestID)
            return .object(["settled": .bool(false), "payload": .null, "error": .null,
                "provenance": .string("external_unverified")])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class ProductCoordinationTests: XCTestCase {
    func testDTOsKeepUnknownCostAndRejectNumericMoneyAuthorityAndMixedScopes() throws {
        let value = try ProductCoordinationSnapshot(CoordinationFixture.snapshot(budget: CoordinationFixture.budget(),
            messages: [CoordinationFixture.message()]), expectedScope: CoordinationFixture.scope)
        XCTAssertNil(value.budget?.actualCost); XCTAssertEqual(value.budget?.costKnown, false)
        XCTAssertEqual(value.budget?.actualTokens, 10); XCTAssertEqual(value.inbox.count, 1)
        XCTAssertTrue(value.isOwner); XCTAssertTrue(value.mayWrite)
        let invalidCosts: [RPCValue] = [.number(0), .number(0.1), .string("NaN"), .string("1e-6"), .string("-1")]
        for cost in invalidCosts {
            XCTAssertThrowsError(try ProductBudgetSnapshot(CoordinationFixture.budget(["actual_cost": cost])))
        }
        XCTAssertThrowsError(try ProductBudgetSnapshot(CoordinationFixture.budget(["cost_known": .bool(true)])))
        for key in ["grants_included", "effect_verified", "permits_replay"] {
            XCTAssertThrowsError(try ProductAgentMessage(CoordinationFixture.message([key: .bool(true)])))
        }
        XCTAssertThrowsError(try ProductAgentMessage(CoordinationFixture.message(["body_sha256": .string("bad")])) )
        XCTAssertThrowsError(try ProductCoordinationSnapshot(
            CoordinationFixture.snapshot(messages: [CoordinationFixture.message(["temporary": .bool(true)])]),
            expectedScope: CoordinationFixture.scope))
        XCTAssertThrowsError(try ProductCoordinationSnapshot(CoordinationFixture.snapshot(),
            expectedScope: SessionScope(sessionID: CoordinationFixture.other, temporary: false)))
    }

    func testSnapshotRejectsUnrelatedClientAndUnreviewedBindingGeneration() {
        let original = CoordinationFixture.snapshot()
        XCTAssertThrowsError(try ProductCoordinationSnapshot(
            CoordinationFixture.replacing(original, ["client_id": .string(CoordinationFixture.peer)]),
            expectedScope: CoordinationFixture.scope))
        XCTAssertThrowsError(try ProductCoordinationSnapshot(
            CoordinationFixture.replacing(original, ["binding": CoordinationFixture.binding(revision: 2)]),
            expectedScope: CoordinationFixture.scope))
        XCTAssertThrowsError(try ProductSessionBinding(CoordinationFixture.replacing(
            CoordinationFixture.binding(), ["permissions_inherited": .bool(true)])))
    }

    @MainActor func testNativeOrganizationUsesScopeCASAndExactDecimalString() async throws {
        let wire = CoordinationWire(), store = ProductCoordinationStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(CoordinationFixture.scope); await store.refresh()
        let changed = await store.bind(workspaceID: CoordinationFixture.workspace, group: "Proyecto", pinned: true)
        XCTAssertTrue(changed); XCTAssertEqual(store.snapshot?.binding.revision, 2)
        let binding = await wire.params("product.coordination")
        XCTAssertEqual(binding?["session_id"], .string(CoordinationFixture.session))
        XCTAssertEqual(binding?["arguments"]?["expected_revision"], .number(1))
        XCTAssertEqual(binding?["arguments"]?["group"], .string("Proyecto"))
        let configured = await store.configureBudget(tokens: "100", cost: "0.100000000001")
        XCTAssertTrue(configured)
        let budget = await wire.params("product.coordination")
        XCTAssertEqual(budget?["arguments"]?["cost_limit"], .string("0.100000000001"))
        XCTAssertEqual(budget?["arguments"]?["token_limit"], .number(100))
        let before = await wire.count("product.coordination")
        _ = await store.configureBudget(tokens: "1.5", cost: "0.1")
        _ = await store.configureBudget(tokens: "100", cost: "1e-5")
        let after = await wire.count("product.coordination")
        XCTAssertEqual(after, before)
    }

    @MainActor func testLostMetadataResponseOnlyRefreshesAndNeverReplaysMutation() async {
        let wire = CoordinationWire(), store = ProductCoordinationStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(CoordinationFixture.scope); await store.refresh(); await wire.losingChange()
        _ = await store.bind(workspaceID: CoordinationFixture.workspace, group: "Conservar", pinned: false)
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertTrue(store.blocksInteraction)
        await store.refresh()
        XCTAssertEqual(store.snapshot?.binding.group, "Conservar")
        XCTAssertTrue(store.responseUnconfirmed)
        store.acknowledgeUnconfirmed()
        XCTAssertFalse(store.responseUnconfirmed)
        let writes = await wire.count("product.coordination")
        XCTAssertEqual(writes, 1)
    }

    @MainActor func testLateSnapshotCannotReplaceAnotherSelectedConversation() async {
        let wire = CoordinationWire(), store = ProductCoordinationStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(CoordinationFixture.scope); await wire.holdNextSnapshot()
        let first = Task { await store.refresh() }
        for _ in 0..<1000 {
            if await wire.isHolding { break }; await Task.yield()
        }
        let holding = await wire.isHolding; XCTAssertTrue(holding)
        let other = SessionScope(sessionID: CoordinationFixture.other, temporary: false)
        store.setScope(other); await store.refresh()
        await wire.releaseSnapshot(); await first.value
        XCTAssertEqual(store.scope, other); XCTAssertEqual(store.snapshot?.binding.scope, other)
        XCTAssertFalse(store.isRequesting)
    }

    @MainActor func testLostSendStartPreservesIdentityAndOnlyPollsExistingRun() async throws {
        let wire = CoordinationWire(), store = ProductCoordinationStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(CoordinationFixture.scope); await store.refresh(); await wire.losingStart()
        let started = await store.send(to: SessionScope(sessionID: CoordinationFixture.other, temporary: false),
            clientID: CoordinationFixture.peer, body: "Mensaje sintético")
        XCTAssertNil(started); XCTAssertTrue(store.responseUnconfirmed)
        let identity = try XCTUnwrap(store.pendingRequestID)
        await store.refresh(); await store.refresh()
        XCTAssertEqual(store.pendingRunID, identity)
        let starts = await wire.count("product.tool.start"), reads = await wire.count("product.tool.result")
        XCTAssertEqual(starts, 1); XCTAssertEqual(reads, 2)
        let params = await wire.params("product.tool.start")
        XCTAssertEqual(params?["tool"], .string("agent_message_send"))
        XCTAssertEqual(params?["request_id"], .string(identity))
        XCTAssertEqual(params?["arguments"]?["recipient_client_id"], .string(CoordinationFixture.peer))
        XCTAssertNil(params?["arguments"]?["grant"].object)
    }
}
