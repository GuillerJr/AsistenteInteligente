import Foundation
import XCTest
@testable import DesktopCore
@testable import JarvisApp

private enum DiscoveryStoreFixture {
    static let nonce = String(repeating: "N", count: 43)
    static let configuration = String(repeating: "b", count: 64)
    static let session = String(repeating: "a", count: 32)
    static var privateRoute: RPCValue { .object(["chat_id": .number(73), "agent_id": .string("jarvis"),
        "session_id": .string(session), "revision": .number(1), "enabled": .bool(true)]) }
    static var review: RPCValue { .object(["owner_chat_id": .number(73), "agent_id": .string("jarvis"),
        "agent_revision": .number(1), "bot_id": .number(72501), "username": .string("jarvis_prueba"),
        "workspace_id": .string(String(repeating: "c", count: 64)), "profile_id": .string(String(repeating: "d", count: 32)),
        "configuration_hash": .string(configuration), "allow_attachments": .bool(false),
        "max_seconds": .number(180), "consumes_ignored_updates": .bool(true), "requires_exclusive_bot": .bool(true),
        "telegram_retains_start_message": .bool(true), "review_hash": .string(String(repeating: "e", count: 64))]) }
    static func snapshot(confirmed: Bool = false) -> RPCValue {
        var routes = [privateRoute]
        if confirmed { routes.append(.object(["chat_id": .number(-100123), "agent_id": .string("jarvis"),
            "session_id": .string(String(repeating: "f", count: 32)), "revision": .number(1), "enabled": .bool(true),
            "chat_type": .string("supergroup"), "allowed_sender_ids": .array([.number(73)]),
            "message_thread_id": .number(8), "allow_attachments": .bool(false)])) }
        return .object(["connected": .bool(true), "running": .bool(false), "bot_id": .number(72501), "username": .string("jarvis_prueba"),
            "routes": .array(routes), "configuration_hash": .string(confirmed ? String(repeating: "f", count: 64) : configuration),
            "workspace_id": .string(String(repeating: "c", count: 64)), "pinned_workspace_id": .null,
            "error": .null, "restored_pairing_required_chat_ids": .array([])])
    }
    static func discovery(_ state: String) -> RPCValue {
        let idle = state == "idle", found = ["claimed", "confirmed"].contains(state)
        return .object(["state": .string(state), "challenge_id": idle ? .null : .string(String(repeating: "1", count: 32)),
            "claim_id": state == "claimed" ? .string(String(repeating: "2", count: 32)) : .null,
            "owner_chat_id": idle ? .null : .number(73), "chat_id": found ? .number(-100123) : .null,
            "chat_type": found ? .string("supergroup") : .null, "message_thread_id": found ? .number(8) : .null,
            "sender_id": found ? .number(73) : .null, "agent_id": idle ? .null : .string("jarvis"),
            "agent_revision": idle ? .null : .number(1), "configuration_hash": idle ? .null : .string(configuration),
            "allow_attachments": .bool(false), "error_code": .null, "expires_in_seconds": idle ? .null : .number(180)])
    }
    static func pairing(group: Bool = false) -> RPCValue {
        var value: [String: RPCValue] = ["state": .string("idle"), "challenge_id": .null, "claim_id": .null,
            "chat_id": .null, "agent_id": .null, "agent_revision": .null, "bot_id": .null,
            "username": .null, "expires_at": .null, "error": .null]
        if group { value.merge(["target_chat_id": .null, "chat_type": .null, "allowed_sender_ids": .array([]),
                               "message_thread_id": .null, "route_revision": .null, "sender_id": .null]) { _, new in new } }
        return .object(value)
    }
}

private actor DiscoveryStoreWire {
    var state = "idle"
    var methods: [String] = []
    var holdStart = false
    private var started = false
    private var startWaiter: CheckedContinuation<Void, Never>?
    private var startRelease: CheckedContinuation<Void, Never>?
    func setHold() { holdStart = true }
    func claim() { state = "claimed" }
    func waitForStart() async { if !started { await withCheckedContinuation { startWaiter = $0 } } }
    func releaseStart() { startRelease?.resume(); startRelease = nil }
    func recorded() -> [String] { methods }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        methods.append(method)
        switch method {
        case "telegram.status": return DiscoveryStoreFixture.snapshot(confirmed: state == "confirmed")
        case "telegram.history": return .object(["events": .array([]), "limit": .number(50), "capacity": .number(2048)])
        case "telegram.pairing.status": return DiscoveryStoreFixture.pairing()
        case "telegram.group.pairing.status": return DiscoveryStoreFixture.pairing(group: true)
        case "telegram.group.discover.review": return DiscoveryStoreFixture.review
        case "telegram.group.discover.status": return DiscoveryStoreFixture.discovery(state)
        case "telegram.group.discover.start":
            state = "listening"; started = true; startWaiter?.resume(); startWaiter = nil
            if holdStart { await withCheckedContinuation { startRelease = $0 } }
            return DiscoveryStoreFixture.discovery(state)
        case "telegram.group.discover.command":
            return .object(["challenge_id": .string(String(repeating: "1", count: 32)),
                "command": .string("/jarvis_pair@jarvis_prueba " + DiscoveryStoreFixture.nonce)])
        case "telegram.group.discover.confirm":
            state = "confirmed"; return DiscoveryStoreFixture.snapshot(confirmed: true)
        case "telegram.group.discover.cancel":
            state = "cancelled"; return DiscoveryStoreFixture.discovery(state)
        default: throw RPCFailure.invalidResponse
        }
    }
}

@MainActor private final class DiscoveryStoreCopier: TelegramEphemeralCommandCopier {
    var commands: [String] = []
    var current: String?
    func copyTelegramCommand(_ command: String) -> Bool { commands.append(command); current = command; return true }
    func clearOwnedCommand() { current = nil }
}

final class TelegramDiscoveryStoreTests: XCTestCase {
    @MainActor private func makeStore(_ wire: DiscoveryStoreWire, copier: DiscoveryStoreCopier,
                                  confirm: @escaping TelegramStore.Confirmation) throws -> TelegramStore {
        let request: MaintenanceClient.Request = { try await wire.request($0, $1) }
        let store = TelegramStore(client: TelegramClient(request: request), pairingClient: TelegramPairingClient(request: request),
            groupPairingClient: TelegramGroupPairingClient(request: request), groupDiscoveryClient: TelegramGroupDiscoveryClient(request: request),
            isActive: { true }, commandCopier: DiscoveryStoreCopier(), discoveryCopier: copier, confirmReview: confirm)
        store.snapshot = try TelegramSnapshot(DiscoveryStoreFixture.snapshot())
        return store
    }
    @MainActor func testDeclinedNativeReviewDoesNotStartPollOrCopyCommand() async throws {
        let wire = DiscoveryStoreWire(), copier = DiscoveryStoreCopier()
        var reviews = 0
        let store = try makeStore(wire, copier: copier) { _, _, _, current in reviews += 1; XCTAssertTrue(current()); return false }
        let candidate = await store.reviewGroupDiscovery(ownerChatID: 73, agentID: "jarvis", allowAttachments: false)
        let review = try XCTUnwrap(candidate)
        let started = await store.startGroupDiscovery(review, updateConsumption: true, noOtherConsumer: true)
        XCTAssertFalse(started); XCTAssertEqual(reviews, 1); XCTAssertTrue(copier.commands.isEmpty)
        let calls = await wire.recorded()
        XCTAssertEqual(calls, ["telegram.group.discover.review"])
    }
    @MainActor func testDiscoveryReviewsEachEffectAndPreservesPrivateSessionWithoutExposingNonce() async throws {
        let wire = DiscoveryStoreWire(), copier = DiscoveryStoreCopier()
        var details: [String] = []
        let store = try makeStore(wire, copier: copier) { _, _, detail, current in details.append(detail); return current() }
        let candidate = await store.reviewGroupDiscovery(ownerChatID: 73, agentID: "jarvis", allowAttachments: false)
        let review = try XCTUnwrap(candidate)
        let started = await store.startGroupDiscovery(review, updateConsumption: true, noOtherConsumer: true)
        XCTAssertTrue(started); XCTAssertTrue(store.pairingActive)
        await store.copyGroupDiscoveryCommand()
        XCTAssertEqual(copier.commands.count, 1)
        XCTAssertEqual(copier.current, "/jarvis_pair@jarvis_prueba " + DiscoveryStoreFixture.nonce)
        await wire.claim(); await store.refreshFromUI()
        // A poll changes the locally derived expiration Date. Confirmation is
        // bound to claim/configuration identity, not equality of that Date.
        let earlier = try TelegramGroupDiscoveryStatus(DiscoveryStoreFixture.discovery("claimed"), now: Date().addingTimeInterval(-1))
        await store.confirmGroupDiscovery(earlier)
        XCTAssertEqual(store.groupDiscovery?.state, "confirmed"); XCTAssertFalse(store.pairingActive)
        XCTAssertEqual(store.snapshot?.routes.count, 2); XCTAssertEqual(store.snapshot?.running, false)
        XCTAssertEqual(store.snapshot?.routes.first?.sessionID, DiscoveryStoreFixture.session)
        XCTAssertTrue(store.discoveryNotice?.contains("antes de añadir otro grupo") == true)
        XCTAssertNil(copier.current); XCTAssertEqual(details.count, 3)
        XCTAssertTrue(details.allSatisfy { !$0.contains(DiscoveryStoreFixture.nonce) })
        let calls = await wire.recorded()
        XCTAssertEqual(calls.filter { $0 == "telegram.group.discover.command" }.count, 1)
        XCTAssertEqual(calls.filter { $0 == "telegram.group.discover.confirm" }.count, 1)
        XCTAssertFalse(calls.contains(where: { $0.contains("sendMessage") }))
    }
    @MainActor func testCloseDuringUncertainStartCannotPublishOldProfileState() async throws {
        let wire = DiscoveryStoreWire(), copier = DiscoveryStoreCopier()
        await wire.setHold()
        let store = try makeStore(wire, copier: copier) { _, _, _, current in current() }
        let review = try TelegramGroupDiscoveryReview(DiscoveryStoreFixture.review)
        let start = Task { await store.startGroupDiscovery(review, updateConsumption: true, noOtherConsumer: true) }
        await wire.waitForStart()
        await store.close(); await wire.releaseStart()
        let accepted = await start.value
        XCTAssertFalse(accepted); XCTAssertNil(store.snapshot); XCTAssertNil(store.groupDiscovery)
        XCTAssertNil(store.discoveryReview); XCTAssertNil(copier.current); XCTAssertFalse(store.isChanging)
        let calls = await wire.recorded()
        XCTAssertEqual(calls.filter { $0 == "telegram.group.discover.start" }.count, 1)
    }
}
