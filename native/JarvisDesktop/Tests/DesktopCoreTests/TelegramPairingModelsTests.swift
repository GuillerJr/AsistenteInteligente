import Foundation
import XCTest
@testable import DesktopCore

final class TelegramPairingModelsTests: XCTestCase {
    private var review: [String: RPCValue] {
        ["agent_id": .string("jarvis"), "agent_revision": .number(1), "bot_id": .number(72501), "username": .string("jarvis_prueba"),
         "workspace_id": .string(String(repeating: "a", count: 64)), "review_hash": .string(String(repeating: "b", count: 64)),
         "max_seconds": .number(180), "consumes_ignored_updates": .bool(true), "requires_exclusive_bot": .bool(true),
         "telegram_retains_start_message": .bool(true)]
    }
    private var status: [String: RPCValue] {
        ["state": .string("listening"), "challenge_id": .string(String(repeating: "a", count: 32)), "claim_id": .null,
         "chat_id": .null, "agent_id": .string("jarvis"), "agent_revision": .number(1), "bot_id": .number(72501),
         "username": .string("jarvis_prueba"), "expires_at": .number(2_000_000_000), "error": .null]
    }

    func testReviewRequiresExplicitConsumptionExclusivityAndExternalRetentionWarnings() throws {
        let validated = try TelegramPairingReview(.object(review))
        XCTAssertEqual(validated.agentRevision, 1)
        for change in [["consumes_ignored_updates": RPCValue.bool(false)], ["requires_exclusive_bot": .bool(false)],
                       ["telegram_retains_start_message": .bool(false)], ["max_seconds": .number(181)],
                       ["telegram_uri": .string("tg://synthetic-private")], ["review_hash": .string("invalid")]] {
            XCTAssertThrowsError(try TelegramPairingReview(.object(review.merging(change) { _, value in value })))
        }
        XCTAssertFalse(ManagementValidation.json(validated.reviewValue).contains("tg://"))
    }

    func testClaimAndChallengeAreSeparateAndOnlyActiveStatesExposeProposedChatMetadata() throws {
        XCTAssertTrue(try TelegramPairingStatus(.object(status)).active)
        var claim = status
        claim["state"] = .string("claimed"); claim["claim_id"] = .string(String(repeating: "c", count: 32)); claim["chat_id"] = .number(4567)
        let valid = try TelegramPairingStatus(.object(claim))
        XCTAssertNotEqual(valid.claimID, valid.challengeID)
        XCTAssertFalse(ManagementValidation.json(valid.claimReviewValue).contains("tg://"))
        for change in [["claim_id": RPCValue.string(String(repeating: "a", count: 32))], ["state": .string("confirmed")],
                       ["chat_id": .number(-1)], ["nonce": .string("SYNTHETIC_PRIVATE_NONCE")], ["username": .string("valid_name\n")]] {
            XCTAssertThrowsError(try TelegramPairingStatus(.object(claim.merging(change) { _, value in value })))
        }
        var expired = status
        expired["state"] = .string("expired")
        XCTAssertFalse(try TelegramPairingStatus(.object(expired)).active)
    }

    func testPairingErrorsDoNotExposeAnOpaqueLinkOrClaimThatCleanupWasConfirmed() {
        for message in ["tg://resolve?domain=synthetic&start=PRIVATE", "opaque diagnostic"] {
            XCTAssertFalse(TelegramPresentation.errorMessage(RPCFailure.remote(-32035, message)).contains(message))
        }
        XCTAssertTrue(TelegramPairingOpenFailure.cleanupUnconfirmed.localizedDescription.contains("No se confirmó"))
        XCTAssertTrue(TelegramPairingOpenFailure.alreadyAttempted.localizedDescription.contains("inicia otro"))
    }
}
