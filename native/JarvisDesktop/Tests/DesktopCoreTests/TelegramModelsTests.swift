import Foundation
import XCTest
@testable import DesktopCore

final class TelegramModelsTests: XCTestCase {
    private let secret = "72501:SENTINEL_JARVIS_TOKEN_ONLY_IN_RAM"
    private let session = String(repeating: "a", count: 32)
    private var route: RPCValue {
        .object(["chat_id": .number(4567), "agent_id": .string("jarvis"), "session_id": .string(session),
                 "revision": .number(1), "enabled": .bool(true)])
    }
    private var status: [String: RPCValue] {
        ["connected": .bool(true), "running": .bool(false), "bot_id": .number(72501), "username": .string("jarvis_prueba"),
         "routes": .array([route]), "configuration_hash": .string(String(repeating: "b", count: 64)),
         "workspace_id": .string(String(repeating: "c", count: 64)), "pinned_workspace_id": .null, "error": .null]
    }
    private var event: [String: RPCValue] {
        ["update_id": .number(1), "epoch": .number(0), "chat_id": .number(4567), "message_id": .number(2),
         "agent_id": .string("jarvis"), "session_id": .string(session), "route_revision": .number(1),
         "status": .string("completed"), "outcome": .string("known"), "run_id": .string(String(repeating: "d", count: 32)),
         "error_code": .null, "outgoing_message_id": .null, "reply_sha256": .null, "reply_truncated": .bool(false)]
    }

    func testTokenAndPrivateChatInputHaveBoundedExactFormats() {
        XCTAssertTrue(TelegramValidation.token(secret))
        for invalid in [secret + "\n", " " + secret, "0:" + String(repeating: "a", count: 20),
                        "1:" + String(repeating: "a", count: 19), "1:" + String(repeating: "a", count: 129)] {
            XCTAssertFalse(TelegramValidation.token(invalid))
        }
        XCTAssertEqual(TelegramValidation.chatID("4503599627370495"), TelegramValidation.maximumIdentity)
        for invalid in ["-123", "0", "+12", "1e3", "12.0", "4503599627370496", "999999999999999999999"] {
            XCTAssertNil(TelegramValidation.chatID(invalid))
        }
    }

    func testReviewedSnapshotBindsFiniteBotRoutesAndWorkspaceWithoutSecretPayloads() throws {
        let snapshot = try TelegramSnapshot(.object(status))
        let review = ManagementValidation.json(snapshot.reviewValue)
        XCTAssertTrue(review.contains(session))
        XCTAssertTrue(review.contains(snapshot.workspaceID))
        XCTAssertTrue(review.contains(snapshot.configurationHash))
        XCTAssertFalse(review.contains(secret))
        for change in [["routes": RPCValue.array([route, route])], ["routes": .array(Array(repeating: route, count: 33))],
                       ["token": .string(secret)], ["running": .bool(true)], ["connected": .bool(false)],
                       ["username": .string("valid_name\n")], ["error": .string(secret)]] {
            XCTAssertThrowsError(try TelegramSnapshot(.object(status.merging(change) { _, value in value })))
        }
        var running = status
        running["running"] = .bool(true); running["pinned_workspace_id"] = running["workspace_id"]
        XCTAssertTrue(try TelegramSnapshot(.object(running)).running)
        running["pinned_workspace_id"] = .string(String(repeating: "f", count: 64))
        XCTAssertThrowsError(try TelegramSnapshot(.object(running)))
    }

    func testHistoryPreservesUnknownOutcomesAndEpochIdentityWithoutMessages() throws {
        let emptyReply = try TelegramEventSnapshot(.object(event))
        XCTAssertNil(emptyReply.outgoingMessageID)
        XCTAssertNil(emptyReply.replyHash)
        var interrupted = event
        interrupted["epoch"] = .number(1); interrupted["status"] = .string("interrupted"); interrupted["outcome"] = .string("unknown")
        let differentEpoch = try TelegramEventSnapshot(.object(interrupted))
        XCTAssertNotEqual(emptyReply.id, differentEpoch.id)
        let catalog = try TelegramHistorySnapshot(.object(["events": .array([.object(event), .object(interrupted)]),
                                                          "limit": .number(50), "capacity": .number(2048)]))
        XCTAssertEqual(catalog.events.count, 2)
        for change in [["outcome": RPCValue.string("unknown")], ["text": .string("private message")],
                       ["token": .string(secret)], ["chat_id": .number(-100)], ["run_id": .string("not-a-run")]] {
            XCTAssertThrowsError(try TelegramEventSnapshot(.object(event.merging(change) { _, value in value })))
        }
    }

    func testRemoteTelegramDiagnosticsCannotExposeCredentialsOrMessages() {
        for body in [secret, "private Telegram message", "/private/diagnostics", "future_new_error"] {
            let text = TelegramPresentation.errorMessage(RPCFailure.remote(-32035, body))
            XCTAssertEqual(text, "No se confirmó la operación de Telegram. Actualiza el estado antes de continuar.")
            XCTAssertFalse(text.contains(body))
        }
        XCTAssertTrue(TelegramPresentation.errorMessage(RPCFailure.remote(-32035, "telegram_configuration_changed"))
            .contains("vuelve a revisarlas"))
        XCTAssertEqual(TelegramPresentation.errorMessage(RPCFailure.disconnected), RPCFailure.disconnected.localizedDescription)
    }
}
