import Foundation
import XCTest
@testable import DesktopCore

private enum RestoredPairingFixture {
    static func route(_ id: Int, revision: Int = 1, enabled: Bool = true) -> RPCValue {
        .object(["chat_id": .number(Double(id)), "agent_id": .string("jarvis"),
                 "session_id": .string(String(repeating: id == 42 ? "a" : "b", count: 32)),
                 "revision": .number(Double(revision)), "enabled": .bool(enabled)])
    }
    static func snapshot(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        var value: [String: RPCValue] = ["connected": .bool(true), "running": .bool(false),
            "bot_id": .number(72501), "username": .string("jarvis_synthetic"),
            "routes": .array([route(42), route(84)]), "configuration_hash": .string(String(repeating: "c", count: 64)),
            "workspace_id": .string(String(repeating: "d", count: 64)), "pinned_workspace_id": .null,
            "error": .null, "restored_pairing_required_chat_ids": .array([.number(42), .number(84)])]
        value.merge(changes) { _, new in new }; return .object(value)
    }
    static func claim(_ changes: [String: RPCValue] = [:]) throws -> TelegramPairingStatus {
        var value: [String: RPCValue] = ["state": .string("claimed"),
            "challenge_id": .string(String(repeating: "e", count: 32)),
            "claim_id": .string(String(repeating: "f", count: 32)), "chat_id": .number(42),
            "agent_id": .string("jarvis"), "agent_revision": .number(3), "bot_id": .number(72501),
            "username": .string("jarvis_synthetic"), "expires_at": .number(4_102_444_800), "error": .null]
        value.merge(changes) { _, new in new }; return try TelegramPairingStatus(.object(value))
    }
}

private actor RestoredPairingWire {
    let response: RPCValue
    private var calls: [(String, [String: RPCValue])] = []
    init(_ response: RPCValue) { self.response = response }
    func request(_ method: String, _ params: [String: RPCValue]) -> RPCValue {
        calls.append((method, params)); return response
    }
    func recorded() -> [(String, [String: RPCValue])] { calls }
}

final class TelegramRestoredPairingClientTests: XCTestCase {
    @MainActor
    func testConfirmationConsumesOnlyTheSelectedPendingProofWithExactRouteArguments() async throws {
        let source = try TelegramSnapshot(RestoredPairingFixture.snapshot())
        let wire = RestoredPairingWire(RestoredPairingFixture.snapshot([
            "restored_pairing_required_chat_ids": .array([.number(84)])]))
        let client = TelegramPairingClient(request: { await wire.request($0, $1) })
        let result = try await client.confirmRestored(RestoredPairingFixture.claim(),
            route: source.routes[0], snapshot: source)
        XCTAssertEqual(result.restoredPairingRequiredChatIDs, [84])
        let calls = await wire.recorded()
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0].0, "telegram.restore.pairing.confirm")
        XCTAssertEqual(calls[0].1, ["claim_id": .string(String(repeating: "f", count: 32)),
            "agent_id": .string("jarvis"), "expected_profile_revision": .number(3),
            "chat_id": .number(42), "expected_route_revision": .number(1),
            "configuration_hash": .string(source.configurationHash)])
    }
    @MainActor
    func testExpiredOrAlreadySatisfiedProofNeverDispatchesAConfirmation() async throws {
        for expired in [false, true] {
            let source = try TelegramSnapshot(RestoredPairingFixture.snapshot(expired ? [:] : [
                "restored_pairing_required_chat_ids": .array([.number(84)])]))
            let claim = try RestoredPairingFixture.claim(expired ? ["expires_at": .number(1)] : [:])
            let wire = RestoredPairingWire(RestoredPairingFixture.snapshot())
            let client = TelegramPairingClient(request: { await wire.request($0, $1) })
            do {
                _ = try await client.confirmRestored(claim, route: source.routes[0], snapshot: source)
                XCTFail("An expired or already satisfied claim was sent")
            } catch { XCTAssertEqual(error as? RPCFailure, .configuration) }
            let calls = await wire.recorded(); XCTAssertTrue(calls.isEmpty)
        }
    }
    @MainActor
    func testResponseCannotConsumeAnotherRoutesProofOrChangeItsIdentity() async throws {
        let source = try TelegramSnapshot(RestoredPairingFixture.snapshot())
        let changes: [[String: RPCValue]] = [
            ["restored_pairing_required_chat_ids": .array([])],
            ["restored_pairing_required_chat_ids": .array([.number(42)])],
            ["restored_pairing_required_chat_ids": .array([.number(42), .number(84)])],
            ["restored_pairing_required_chat_ids": .array([.number(84)]), "routes": .array([
                RestoredPairingFixture.route(42, revision: 2), RestoredPairingFixture.route(84)])],
            ["restored_pairing_required_chat_ids": .array([.number(84)]), "workspace_id": .string(String(repeating: "0", count: 64))],
        ]
        for change in changes {
            let wire = RestoredPairingWire(RestoredPairingFixture.snapshot(change))
            let client = TelegramPairingClient(request: { await wire.request($0, $1) })
            do {
                _ = try await client.confirmRestored(RestoredPairingFixture.claim(), route: source.routes[0], snapshot: source)
                XCTFail("The response changed another route/proof or the selected workspace")
            } catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
        }
    }
    func testSnapshotRejectsDuplicateForeignAndDisabledPendingProofs() throws {
        for changes: [String: RPCValue] in [
            ["restored_pairing_required_chat_ids": .array([.number(42), .number(42)])],
            ["restored_pairing_required_chat_ids": .array([.number(99)])],
            ["routes": .array([RestoredPairingFixture.route(42, enabled: false), RestoredPairingFixture.route(84)])],
        ] {
            XCTAssertThrowsError(try TelegramSnapshot(RestoredPairingFixture.snapshot(changes)))
        }
    }
}
