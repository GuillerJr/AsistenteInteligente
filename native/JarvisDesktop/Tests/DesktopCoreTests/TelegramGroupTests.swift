import Foundation
import XCTest
@testable import DesktopCore

private enum TelegramGroupFixture {
    static let session = String(repeating: "a", count: 32)
    static let challenge = String(repeating: "d", count: 32)
    static let claim = String(repeating: "e", count: 32)
    static let configuration = String(repeating: "b", count: 64)
    static let nonce = String(repeating: "N", count: 43)
    static var route: [String: RPCValue] {
        ["chat_id": .number(-100123), "agent_id": .string("jarvis"), "session_id": .string(session), "revision": .number(4), "enabled": .bool(true),
         "chat_type": .string("supergroup"), "allowed_sender_ids": .array([.number(73)]), "message_thread_id": .number(8), "allow_attachments": .bool(true)]
    }
    static func snapshot(required: Bool = true, route: [String: RPCValue] = TelegramGroupFixture.route) -> RPCValue {
        .object(["connected": .bool(true), "running": .bool(false), "bot_id": .number(72501), "username": .string("jarvis_prueba"),
            "routes": .array([.object(route)]), "configuration_hash": .string(configuration), "workspace_id": .string(String(repeating: "c", count: 64)),
            "pinned_workspace_id": .null, "error": .null, "restored_pairing_required_chat_ids": .array(required ? [.number(-100123)] : [])])
    }
    static var review: [String: RPCValue] {
        ["agent_id": .string("jarvis"), "agent_revision": .number(2), "bot_id": .number(72501), "username": .string("jarvis_prueba"),
         "workspace_id": .string(String(repeating: "c", count: 64)), "review_hash": .string(String(repeating: "f", count: 64)),
         "max_seconds": .number(180), "consumes_ignored_updates": .bool(true), "requires_exclusive_bot": .bool(true), "telegram_retains_start_message": .bool(true),
         "chat_id": .number(-100123), "chat_type": .string("supergroup"), "allowed_sender_ids": .array([.number(73)]),
         "message_thread_id": .number(8), "route_revision": .number(4), "allow_attachments": .bool(true)]
    }
    static func status(claimed: Bool = false) -> [String: RPCValue] {
        ["state": .string(claimed ? "claimed" : "listening"), "challenge_id": .string(challenge), "claim_id": claimed ? .string(claim) : .null,
         "chat_id": claimed ? .number(-100123) : .null, "agent_id": .string("jarvis"), "agent_revision": .number(2), "bot_id": .number(72501),
         "username": .string("jarvis_prueba"), "expires_at": .number(Date().timeIntervalSince1970 + 180), "error": .null,
         "target_chat_id": .number(-100123), "chat_type": .string("supergroup"), "allowed_sender_ids": .array([.number(73)]),
         "message_thread_id": .number(8), "route_revision": .number(4), "sender_id": claimed ? .number(73) : .null]
    }
    static var event: [String: RPCValue] {
        ["update_id": .number(6), "epoch": .number(1), "chat_id": .number(-100123), "message_id": .number(91), "agent_id": .string("jarvis"),
         "session_id": .string(session), "route_revision": .number(4), "status": .string("dispatching"), "outcome": .string("pending"),
         "run_id": .string(challenge), "error_code": .null, "outgoing_message_id": .null, "reply_sha256": .null, "reply_truncated": .bool(false),
         "chat_type": .string("supergroup"), "sender_id": .number(73), "message_thread_id": .number(8), "attachment_count": .number(1)]
    }
}

private actor TelegramGroupWire {
    var calls: [(String, [String: RPCValue])] = []
    var replies: [RPCValue]
    init(_ replies: [RPCValue]) { self.replies = replies }
    func request(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        calls.append((method, params)); guard !replies.isEmpty else { throw RPCFailure.disconnected }; return replies.removeFirst()
    }
    func recorded() -> [(String, [String: RPCValue])] { calls }
}

@MainActor private final class TelegramGroupCopier: TelegramNativeCommandCopier {
    var commands: [String] = []
    var success = true
    func copyTelegramCommand(_ command: String) -> Bool { commands.append(command); return success }
}

@MainActor final class TelegramGroupTests: XCTestCase {
    func testGroupPoliciesRejectSignedConfusionAndPartialSchemas() throws {
        let row = try TelegramRouteSnapshot(.object(TelegramGroupFixture.route))
        XCTAssertEqual(row.chatID, -100123); XCTAssertEqual(row.policy.allowedSenderIDs, [73]); XCTAssertEqual(row.policy.messageThreadID, 8)
        XCTAssertEqual(row.reviewValue, .object(TelegramGroupFixture.route))
        for change: [String: RPCValue] in [
            ["chat_id": .number(100123)], ["chat_type": .string("private")], ["allowed_sender_ids": .array([])],
            ["allowed_sender_ids": .array([.number(73), .number(73)])], ["allowed_sender_ids": .array([.bool(true)])],
            ["message_thread_id": .number(0)], ["chat_type": .string("group")], ["thread_id": .number(8)], ["allow_attachments": .string("true")]
        ] { XCTAssertThrowsError(try TelegramRouteSnapshot(.object(TelegramGroupFixture.route.merging(change) { _, new in new }))) }
        var missing = TelegramGroupFixture.route; missing.removeValue(forKey: "message_thread_id")
        XCTAssertThrowsError(try TelegramRouteSnapshot(.object(missing)))
        XCTAssertNil(TelegramValidation.chatID("-100123")); XCTAssertEqual(TelegramValidation.groupChatID("-100123"), -100123)
        for text in ["100123", "-0", "--1", "-1e3", "-4503599627370496"] { XCTAssertNil(TelegramValidation.groupChatID(text)) }
    }
    func testSnapshotRequiresExactExistingEnabledGroupAndNeverLeaksNonce() throws {
        let status = try TelegramSnapshot(TelegramGroupFixture.snapshot())
        XCTAssertEqual(status.restoredPairingRequiredChatIDs, [-100123])
        XCTAssertFalse(ManagementValidation.json(status.reviewValue).contains(TelegramGroupFixture.nonce))
        var disabled = TelegramGroupFixture.route; disabled["enabled"] = .bool(false)
        XCTAssertThrowsError(try TelegramSnapshot(TelegramGroupFixture.snapshot(route: disabled)))
        var wrong = TelegramGroupFixture.snapshot().object!; wrong["restored_pairing_required_chat_ids"] = .array([.number(-999)])
        XCTAssertThrowsError(try TelegramSnapshot(.object(wrong)))
    }
    func testEventContextIsAtomicBoundedAndSignedWithSpecificCancellation() throws {
        let event = try TelegramEventSnapshot(.object(TelegramGroupFixture.event))
        XCTAssertTrue(event.canCancel); XCTAssertEqual(event.senderID, 73); XCTAssertEqual(event.messageThreadID, 8); XCTAssertEqual(event.attachmentCount, 1)
        for change: [String: RPCValue] in [["chat_type": .string("private")], ["sender_id": .number(-73)], ["attachment_count": .number(2)],
                                          ["message_thread_id": .bool(true)], ["chat_type": .string("group")], ["text": .string("private")]] {
            XCTAssertThrowsError(try TelegramEventSnapshot(.object(TelegramGroupFixture.event.merging(change) { _, new in new })))
        }
        var missing = TelegramGroupFixture.event; missing.removeValue(forKey: "sender_id")
        XCTAssertThrowsError(try TelegramEventSnapshot(.object(missing)))
    }
    func testGroupClaimBindsDestinationSenderAndNeverTreatsItAsPrivate() throws {
        let claim = try TelegramGroupPairingStatus(.object(TelegramGroupFixture.status(claimed: true)))
        XCTAssertEqual(claim.chatID, -100123); XCTAssertEqual(claim.senderID, 73)
        XCTAssertEqual(claim.claimReviewValue["chat_id"].integer, -100123)
        for change: [String: RPCValue] in [["chat_id": .number(-100124)], ["sender_id": .number(99)], ["sender_id": .null],
                                          ["target_chat_id": .number(100123)], ["command": .string(TelegramGroupFixture.nonce)]] {
            XCTAssertThrowsError(try TelegramGroupPairingStatus(.object(TelegramGroupFixture.status(claimed: true).merging(change) { _, new in new })))
        }
        XCTAssertThrowsError(try TelegramPairingStatus(.object(TelegramGroupFixture.status(claimed: true))))
    }
    func testGroupStartRequiresBothAcknowledgmentsAndExactReviewBindings() async throws {
        let wire = TelegramGroupWire([.object(TelegramGroupFixture.status())])
        let client = TelegramGroupPairingClient(request: { try await wire.request($0, $1) })
        let review = try TelegramGroupPairingReview(.object(TelegramGroupFixture.review))
        do { _ = try await client.start(review, updateConsumption: true, noOtherConsumer: false); XCTFail("Must refuse missing review") } catch {}
        let unreviewedCalls = await wire.recorded(); XCTAssertTrue(unreviewedCalls.isEmpty)
        _ = try await client.start(review, updateConsumption: true, noOtherConsumer: true)
        let calls = await wire.recorded(); XCTAssertEqual(calls.count, 1); XCTAssertEqual(calls[0].0, "telegram.group.pairing.start")
        XCTAssertEqual(calls[0].1["chat_id"], .number(-100123)); XCTAssertEqual(calls[0].1["expected_route_revision"], .number(4))
        XCTAssertEqual(calls[0].1["expected_profile_revision"], .number(2)); XCTAssertNil(calls[0].1["command"])
    }
    func testOneUseGroupCommandGoesDirectlyToNativeCopierWithoutTelegramSend() async throws {
        let command = "/jarvis_pair@jarvis_prueba " + TelegramGroupFixture.nonce
        let wire = TelegramGroupWire([.object(["challenge_id": .string(TelegramGroupFixture.challenge), "command": .string(command)])])
        let client = TelegramGroupPairingClient(request: { try await wire.request($0, $1) }); let copier = TelegramGroupCopier()
        let status = try TelegramGroupPairingStatus(.object(TelegramGroupFixture.status()))
        try await client.copy(status, using: copier)
        XCTAssertEqual(copier.commands, [command])
        do { try await client.copy(status, using: copier); XCTFail("One attempt only") } catch { XCTAssertEqual(error as? TelegramGroupCommandFailure, .alreadyAttempted) }
        let calls = await wire.recorded(); XCTAssertEqual(calls.count, 1); XCTAssertEqual(calls[0].0, "telegram.group.pairing.command")
        XCTAssertEqual(calls[0].1, ["challenge_id": .string(TelegramGroupFixture.challenge)])
    }
    func testInvalidGroupCommandCancelsAndNeverCopiesOrRetries() async throws {
        var cancelled = TelegramGroupFixture.status(); cancelled["state"] = .string("cancelled")
        let wire = TelegramGroupWire([.object(["challenge_id": .string(TelegramGroupFixture.challenge), "command": .string("/jarvis_pair@other " + TelegramGroupFixture.nonce)]), .object(cancelled)])
        let client = TelegramGroupPairingClient(request: { try await wire.request($0, $1) }); let copier = TelegramGroupCopier()
        do { try await client.copy(TelegramGroupPairingStatus(.object(TelegramGroupFixture.status())), using: copier); XCTFail("Wrong bot") }
        catch { XCTAssertEqual(error as? TelegramGroupCommandFailure, .invalidCommand) }
        XCTAssertTrue(copier.commands.isEmpty)
        let calls = await wire.recorded(); XCTAssertEqual(calls.map(\.0), ["telegram.group.pairing.command", "telegram.group.pairing.cancel"])
    }
    func testClaimConfirmationRemovesOnlyThisFreshProofAndRetainsConversation() async throws {
        let wire = TelegramGroupWire([TelegramGroupFixture.snapshot(required: false)])
        let client = TelegramGroupPairingClient(request: { try await wire.request($0, $1) })
        let source = try TelegramSnapshot(TelegramGroupFixture.snapshot()); let claim = try TelegramGroupPairingStatus(.object(TelegramGroupFixture.status(claimed: true)))
        let result = try await client.confirm(claim, route: source.routes[0], snapshot: source)
        XCTAssertEqual(result.routes, source.routes); XCTAssertTrue(result.restoredPairingRequiredChatIDs.isEmpty)
        let calls = await wire.recorded(); XCTAssertEqual(calls[0].0, "telegram.group.pairing.confirm")
        XCTAssertEqual(calls[0].1["configuration_hash"], .string(source.configurationHash)); XCTAssertEqual(calls[0].1.count, 5)
    }
    func testRoutePolicyUpdateUsesCASAndRejectsChangedConversationReceipt() async throws {
        let route = try TelegramRouteSnapshot(.object(TelegramGroupFixture.route))
        let policy = try TelegramRoutePolicy(chatType: .supergroup, allowedSenderIDs: [73], messageThreadID: 8, allowAttachments: false)
        var changed = TelegramGroupFixture.route; changed["revision"] = .number(5); changed["allow_attachments"] = .bool(false)
        let wire = TelegramGroupWire([TelegramGroupFixture.snapshot(route: changed)])
        let client = TelegramClient(request: { try await wire.request($0, $1) })
        _ = try await client.updateRoute(route, policy: policy, enabled: true)
        let calls = await wire.recorded(); XCTAssertEqual(calls[0].0, "telegram.routes.update"); XCTAssertEqual(calls[0].1["expected_revision"], .number(4))
        changed["session_id"] = .string(String(repeating: "f", count: 32))
        let invalidWire = TelegramGroupWire([TelegramGroupFixture.snapshot(route: changed)])
        let invalid = TelegramClient(request: { try await invalidWire.request($0, $1) })
        do { _ = try await invalid.updateRoute(route, policy: policy, enabled: true); XCTFail("Changed conversation") } catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
        let invalidCalls = await invalidWire.recorded(); XCTAssertEqual(invalidCalls.count, 1)
    }
    func testCancelExactEventUsesEpochRunAndRouteRevisionAndRejectsWrongReceipt() async throws {
        let event = try TelegramEventSnapshot(.object(TelegramGroupFixture.event))
        let receipt: RPCValue = .object(["run_id": .string(TelegramGroupFixture.challenge), "cancel_requested": .bool(true), "automatic_replay": .bool(false)])
        let wire = TelegramGroupWire([receipt]); let client = TelegramClient(request: { try await wire.request($0, $1) })
        try await client.cancelEvent(event)
        let calls = await wire.recorded(); XCTAssertEqual(calls[0].0, "telegram.events.cancel")
        XCTAssertEqual(calls[0].1, ["epoch": .number(1), "update_id": .number(6), "expected_run_id": .string(TelegramGroupFixture.challenge), "expected_route_revision": .number(4)])
        var bad = receipt.object!; bad["automatic_replay"] = .bool(true)
        let invalidWire = TelegramGroupWire([.object(bad)]); let invalid = TelegramClient(request: { try await invalidWire.request($0, $1) })
        do { try await invalid.cancelEvent(event); XCTFail("Automatic replay forbidden") } catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
        let invalidCalls = await invalidWire.recorded(); XCTAssertEqual(invalidCalls.count, 1)
    }
}
