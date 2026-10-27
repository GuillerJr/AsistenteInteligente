import Foundation
import XCTest
@testable import DesktopCore

private enum SessionCoreFixture {
    static let parent = String(repeating: "a", count: 32)
    static let child = String(repeating: "b", count: 32)
    static let scope = SessionScope(sessionID: parent, temporary: false)
    static func checkpoint(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        var value: [String: RPCValue] = ["checkpoint_id": .string(String(repeating: "c", count: 32)),
            "source_revision": .number(2), "source_digest": .string(String(repeating: "d", count: 64)),
            "through_sequence": .number(2), "kept_message_count": .number(2), "omitted_message_count": .number(0),
            "byte_length": .number(384), "decisions_count": .number(1), "receipts_count": .number(1)]
        value.merge(changes) { _, new in new }; return .object(value)
    }
    static func context(compacted: Bool) -> RPCValue {
        .object(["revision": .number(compacted ? 4 : 2), "message_count": .number(compacted ? 4 : 2),
                 "checkpoint": compacted ? checkpoint() : .null])
    }
    static func session(id: String = parent, parentID: String? = nil) -> RPCValue {
        .object(["session_id": .string(id), "temporary": .bool(false), "title": .string("Conversación sintética"),
                 "agent_id": .string("jarvis"), "archived": .bool(false),
                 "parent_session_id": parentID.map(RPCValue.string) ?? .null])
    }
    static func lineage(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        var value: [String: RPCValue] = ["kind": .string("rewind"), "source_session_id": .string(parent),
            "source_revision": .number(2), "until_sequence": .number(1),
            "source_digest": .string(String(repeating: "d", count: 64)), "effects_reverted": .bool(false),
            "permissions_inherited": .bool(false), "provider_thread_inherited": .bool(false)]
        value.merge(changes) { _, new in new }; return .object(value)
    }
}

private actor SessionCoreWire {
    private var calls: [(String, [String: RPCValue])] = []
    private var compacted = false
    var loseACK = false
    var includeOperation = true
    var lastID: String?
    func setLoseACK(_ value: Bool) { loseACK = value }
    func setIncludeOperation(_ value: Bool) { includeOperation = value }
    func count(_ method: String) -> Int { calls.filter { $0.0 == method }.count }
    func params(_ method: String) -> [String: RPCValue]? { calls.last(where: { $0.0 == method })?.1 }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append((method, params))
        XCTAssertEqual(params["session_id"], .string(SessionCoreFixture.parent))
        switch method {
        case "sessions.operations": return .object(["operations": .array([]), "has_more": .bool(false), "next_offset": .null])
        case "sessions.context": return .object(["context": SessionCoreFixture.context(compacted: compacted)])
        case "sessions.compact":
            XCTAssertEqual(params["expected_revision"], .number(2))
            lastID = params["operation_id"]?.string; compacted = true
            if loseACK { throw RPCFailure.disconnected }
            return .object(["context": SessionCoreFixture.context(compacted: true), "session": SessionCoreFixture.session()])
        case "sessions.operation":
            XCTAssertEqual(params["operation_id"]?.string, lastID)
            if !includeOperation { return .object(["operation": .null]) }
            return .object(["operation": .object(["operation_id": .string(lastID!), "operation_kind": .string("compact"),
                "recorded": .bool(true), "historical_ack_established": .bool(false), "permits_replay": .bool(false),
                "result": .object(["context": SessionCoreFixture.context(compacted: true), "session": SessionCoreFixture.session()])])])
        case "sessions.rewind":
            return .object(["session": SessionCoreFixture.session(id: SessionCoreFixture.child, parentID: SessionCoreFixture.parent), "lineage": SessionCoreFixture.lineage()])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class SessionCoreTests: XCTestCase {
    func testContextCapsuleAndBranchRejectInvalidCountsAndInheritedAuthority() throws {
        XCTAssertEqual(try SessionContextSnapshot(SessionCoreFixture.context(compacted: true)).revision, 4)
        let changes: [[String: RPCValue]] = [
            ["byte_length": .number(16001)], ["source_digest": .string("not-a-digest")],
            ["decisions_count": .number(17)], ["kept_message_count": .number(3)],
            ["through_sequence": .number(0)], ["receipts_count": .number(-1)]
        ]
        for change in changes { XCTAssertThrowsError(try SessionContextCheckpoint(SessionCoreFixture.checkpoint(change))) }
        for key in ["effects_reverted", "permissions_inherited", "provider_thread_inherited"] {
            XCTAssertThrowsError(try SessionBranchSnapshot(
                session: SessionCoreFixture.session(id: SessionCoreFixture.child, parentID: SessionCoreFixture.parent),
                lineage: SessionCoreFixture.lineage([key: .bool(true)]), source: SessionCoreFixture.scope))
        }
        XCTAssertThrowsError(try SessionBranchSnapshot(session: SessionCoreFixture.session(),
            lineage: SessionCoreFixture.lineage(), source: SessionCoreFixture.scope))
    }

    @MainActor func testNativeClientBindsRevisionScopeAndOperationIdentity() async throws {
        let wire = SessionCoreWire(), client = SessionCoreClient(request: { try await wire.request($0, $1) })
        let original = try await client.context(SessionCoreFixture.scope)
        let operation = String(repeating: "e", count: 32)
        let compacted = try await client.compact(SessionCoreFixture.scope, revision: original.revision,
            operationID: operation, decisions: ["Conservar decisión sintética"])
        XCTAssertEqual(compacted.id, operation); XCTAssertFalse(compacted.isHistoricalObservation)
        let params = await wire.params("sessions.compact")
        XCTAssertEqual(params?["retained_decisions"], .array([.string("Conservar decisión sintética")]))
        let branch = try await client.rewind(SessionCoreFixture.scope, revision: 2, until: 1,
            operationID: String(repeating: "f", count: 32), title: "Rama propia")
        XCTAssertEqual(branch.branch?.parentID, SessionCoreFixture.parent)
        XCTAssertEqual(branch.branch?.scope.sessionID, SessionCoreFixture.child)
        do {
            _ = try await client.rewind(SessionCoreFixture.scope, revision: 2, until: 3,
                operationID: operation, title: "Rama inválida")
            XCTFail("Un punto fuera de revisión no se debe enviar.")
        } catch {}
        let count = await wire.count("sessions.rewind"); XCTAssertEqual(count, 1)
    }

    @MainActor func testLostResponseKeepsIdentityAndOnlyInspectsWithoutReplay() async throws {
        let wire = SessionCoreWire(), store = SessionCoreStore()
        store.configure(SessionCoreClient(request: { try await wire.request($0, $1) }))
        store.setScope(SessionCoreFixture.scope); await store.refresh(); await wire.setLoseACK(true)
        await store.compact(decisions: [])
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertTrue(store.blocksInteraction)
        let identity = try XCTUnwrap(store.pendingOperationID)
        store.setScope(SessionScope(sessionID: SessionCoreFixture.child, temporary: false))
        XCTAssertEqual(store.scope, SessionCoreFixture.scope)
        await store.refresh()
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertEqual(store.result?.id, identity)
        XCTAssertEqual(store.result?.isHistoricalObservation, true)
        let writes = await wire.count("sessions.compact"), reads = await wire.count("sessions.operation")
        XCTAssertEqual(writes, 1); XCTAssertEqual(reads, 1)
    }

    @MainActor func testMissingRecordDoesNotConvertUncertaintyIntoSuccess() async {
        let wire = SessionCoreWire(), store = SessionCoreStore()
        store.configure(SessionCoreClient(request: { try await wire.request($0, $1) }))
        store.setScope(SessionCoreFixture.scope); await store.refresh()
        await wire.setLoseACK(true); await wire.setIncludeOperation(false)
        await store.compact(decisions: []); await store.refresh()
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertNil(store.result)
        store.retainUnconfirmed()
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertNil(store.snapshot)
        let writes = await wire.count("sessions.compact"); XCTAssertEqual(writes, 1)
    }

    func testSystemPresentationCannotPromoteModelTextToReceipt() throws {
        let value: RPCValue = .object(["kind": .string("jarvis.session.compaction.v1"), "checkpoint": SessionCoreFixture.checkpoint()])
        let text = "JARVIS_SESSION_CONTEXT_V1\n" + String(decoding: try JSONEncoder().encode(value), as: UTF8.self)
        XCTAssertNotNil(SessionContextPresentation.systemRecord(text, role: "system"))
        XCTAssertNil(SessionContextPresentation.systemRecord(text, role: "assistant"))
        XCTAssertNil(SessionContextPresentation.systemRecord(text, role: "user"))
        XCTAssertNil(SessionContextPresentation.systemRecord("JARVIS_SESSION_CONTEXT_V1\n{}", role: "system"))
    }
}
