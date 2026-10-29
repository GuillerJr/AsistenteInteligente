import Foundation
import XCTest
@testable import DesktopCore

private enum DiscoveryFixture {
    static let configuration = String(repeating: "b", count: 64)
    static let challenge = String(repeating: "d", count: 32)
    static var review: RPCValue { .object([
        "owner_chat_id": .number(73), "agent_id": .string("jarvis"), "agent_revision": .number(2),
        "bot_id": .number(72501), "username": .string("jarvis_prueba"),
        "workspace_id": .string(String(repeating: "c", count: 64)), "profile_id": .string("synthetic_profile"),
        "configuration_hash": .string(configuration), "allow_attachments": .bool(true),
        "max_seconds": .number(180), "consumes_ignored_updates": .bool(true), "requires_exclusive_bot": .bool(true),
        "telegram_retains_start_message": .bool(true), "review_hash": .string(String(repeating: "f", count: 64))]) }
    static func status(claimed: Bool = false) -> [String: RPCValue] {
        ["state": .string(claimed ? "claimed" : "listening"), "challenge_id": .string(challenge),
         "claim_id": claimed ? .string(String(repeating: "e", count: 32)) : .null,
         "owner_chat_id": .number(73), "chat_id": claimed ? .number(-100123) : .null,
         "chat_type": claimed ? .string("supergroup") : .null, "message_thread_id": claimed ? .number(9) : .null,
         "sender_id": claimed ? .number(73) : .null, "agent_id": .string("jarvis"), "agent_revision": .number(2),
         "configuration_hash": .string(configuration), "allow_attachments": .bool(true),
         "error_code": .null, "expires_in_seconds": .number(180)]
    }
    static func snapshot(group: Bool = false) -> RPCValue {
        var routes: [RPCValue] = [.object(["chat_id": .number(73), "agent_id": .string("jarvis"),
            "session_id": .string(String(repeating: "a", count: 32)), "revision": .number(1), "enabled": .bool(true)])]
        if group { routes.append(.object(["chat_id": .number(-100123), "agent_id": .string("jarvis"),
            "session_id": .string(String(repeating: "9", count: 32)), "revision": .number(1), "enabled": .bool(true),
            "chat_type": .string("supergroup"), "allowed_sender_ids": .array([.number(73)]),
            "message_thread_id": .number(9), "allow_attachments": .bool(true)])) }
        return .object(["connected": .bool(true), "running": .bool(false), "bot_id": .number(72501),
            "username": .string("jarvis_prueba"), "routes": .array(routes),
            "configuration_hash": .string(group ? String(repeating: "f", count: 64) : configuration),
            "workspace_id": .string(String(repeating: "c", count: 64)), "pinned_workspace_id": .null,
            "error": .null, "restored_pairing_required_chat_ids": .array([])])
    }
}

private actor DiscoveryWire {
    private var calls: [(String, [String: RPCValue])] = []
    var command = "/jarvis_pair@jarvis_prueba " + String(repeating: "N", count: 43)
    func request(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        calls.append((method, params))
        switch method {
        case "telegram.group.discover.review": return DiscoveryFixture.review
        case "telegram.group.discover.start": return .object(DiscoveryFixture.status())
        case "telegram.group.discover.command": return .object(["challenge_id": .string(DiscoveryFixture.challenge), "command": .string(command)])
        case "telegram.group.discover.confirm": return DiscoveryFixture.snapshot(group: true)
        default: throw RPCFailure.invalidResponse
        }
    }
    func recorded() -> [(String, [String: RPCValue])] { calls }
}

@MainActor private final class DiscoveryCopier: TelegramNativeCommandCopier {
    var values: [String] = []
    func copyTelegramCommand(_ command: String) -> Bool { values.append(command); return true }
}

@MainActor final class TelegramGroupDiscoveryTests: XCTestCase {
    func testMetadataHasNoGroupBeforeClaimAndTypedExactSenderAfterClaim() throws {
        let review = try TelegramGroupDiscoveryReview(DiscoveryFixture.review)
        let pending = try TelegramGroupDiscoveryStatus(.object(DiscoveryFixture.status()))
        XCTAssertTrue(pending.active); XCTAssertNil(pending.chatID); XCTAssertTrue(pending.matches(review))
        let claim = try TelegramGroupDiscoveryStatus(.object(DiscoveryFixture.status(claimed: true)))
        XCTAssertEqual(claim.chatID, -100123); XCTAssertEqual(claim.senderID, review.ownerChatID)
        XCTAssertEqual(claim.chatType, .supergroup); XCTAssertEqual(claim.messageThreadID, 9)
        XCTAssertFalse(ManagementValidation.json(claim.claimReviewValue).contains(String(repeating: "N", count: 43)))
        for changes: [String: RPCValue] in [
            ["sender_id": .number(84)], ["chat_id": .number(100123)], ["chat_type": .string("private")],
            ["chat_type": .string("group")], ["message_thread_id": .bool(true)],
            ["expires_in_seconds": .number(0)], ["nonce": .string("secret")],
            ["owner_chat_id": .bool(true)], ["claim_id": .string(DiscoveryFixture.challenge)]
        ] {
            XCTAssertThrowsError(try TelegramGroupDiscoveryStatus(.object(DiscoveryFixture.status(claimed: true).merging(changes) { _, new in new })))
        }
        var invalid = DiscoveryFixture.review.object!; invalid["consumes_ignored_updates"] = .bool(false)
        XCTAssertThrowsError(try TelegramGroupDiscoveryReview(.object(invalid)))
    }
    func testClientSeparatesReviewStartNativeCopyAndFinalRouteConfirmation() async throws {
        let wire = DiscoveryWire(), copier = DiscoveryCopier()
        let client = TelegramGroupDiscoveryClient(request: { try await wire.request($0, $1) })
        let review = try await client.review(ownerChatID: 73, agentID: "jarvis", allowAttachments: true)
        let started = try await client.start(review, updateConsumption: true, noOtherConsumer: true)
        try await client.copy(started, review: review, using: copier)
        XCTAssertEqual(copier.values.count, 1)
        do { try await client.copy(started, review: review, using: copier); XCTFail("command copied twice") }
        catch { XCTAssertEqual(error as? TelegramGroupCommandFailure, .alreadyAttempted) }
        let claim = try TelegramGroupDiscoveryStatus(.object(DiscoveryFixture.status(claimed: true)))
        let result = try await client.confirm(claim, review: review, snapshot: TelegramSnapshot(DiscoveryFixture.snapshot()))
        XCTAssertFalse(result.running); XCTAssertEqual(result.routes.count, 2)
        let calls = await wire.recorded()
        XCTAssertEqual(calls.map(\.0), ["telegram.group.discover.review", "telegram.group.discover.start", "telegram.group.discover.command", "telegram.group.discover.confirm"])
        XCTAssertEqual(calls.last?.1, ["claim_id": .string(String(repeating: "e", count: 32)),
            "expected_profile_revision": .number(2), "configuration_hash": .string(DiscoveryFixture.configuration)])
    }
    func testInvalidOwnerAndMissingConsumptionDoNotReachTransport() async throws {
        let wire = DiscoveryWire()
        let client = TelegramGroupDiscoveryClient(request: { try await wire.request($0, $1) })
        for owner in [0, -1, Int.max] {
            do { _ = try await client.review(ownerChatID: owner, agentID: "jarvis", allowAttachments: false); XCTFail("invalid owner") } catch {}
        }
        let review = try TelegramGroupDiscoveryReview(DiscoveryFixture.review)
        do { _ = try await client.start(review, updateConsumption: false, noOtherConsumer: true); XCTFail("missing acknowledgement") } catch {}
        let calls = await wire.recorded(); XCTAssertTrue(calls.isEmpty)
    }
}
