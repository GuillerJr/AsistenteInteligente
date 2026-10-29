import XCTest
@testable import DesktopCore

final class LocalVoiceRunReceiptTests: XCTestCase {
    private let run = String(repeating: "a", count: 32)
    private var completed: [String: RPCValue] {
        ["run_id": .string(run), "status": .string("completed"), "outcome": .string("known"), "answer": .string("Cuatro"),
         "error": .null, "error_type": .null, "answer_source": .string("model"), "action_receipts": .array([])]
    }
    private var unknownAction: RPCValue {
        .object(["operation_id": .string("operation1"), "tool_name": .string("workspace_write"), "succeeded": .bool(false), "verified": .bool(false),
            "effect_status": .string("unknown"), "output": .string(""), "error": .string("cleanup_incomplete"), "output_truncated": .bool(false)])
    }
    func testOnlyExactDurableRunCanProduceASpokenReply() throws {
        let receipt = try LocalVoiceRunReceipt(.object(completed), expectedRunID: run)
        XCTAssertTrue(receipt.isSettled); XCTAssertFalse(receipt.requiresApproval)
        XCTAssertEqual(receipt.conversationReply?.runID, run); XCTAssertEqual(receipt.conversationReply?.answer, "Cuatro")
        for change: [String: RPCValue] in [["run_id": .string(String(repeating: "b", count: 32))], ["run_id": .string("stream")],
                                          ["answer_source": .string("stream")], ["answer_source": .null], ["answer": .null],
                                          ["status": .string("unknown")], ["outcome": .string("pending")], ["audio": .string("payload")]] {
            XCTAssertThrowsError(try LocalVoiceRunReceipt(.object(completed.merging(change) { _, new in new }), expectedRunID: run))
        }
    }
    func testApprovalAndCleanupPhasesDoNotBecomeSettledOrSpoken() throws {
        for phase in ["queued", "running", "waiting_approval", "cancelling", "cleaning"] {
            var value = completed; value["status"] = .string(phase); value["outcome"] = .string("pending")
            value["answer"] = .null; value["answer_source"] = .null
            let receipt = try LocalVoiceRunReceipt(.object(value), expectedRunID: run)
            XCTAssertFalse(receipt.isSettled); XCTAssertNil(receipt.conversationReply)
            XCTAssertEqual(receipt.requiresApproval, phase == "waiting_approval")
            value["outcome"] = .string("known")
            XCTAssertThrowsError(try LocalVoiceRunReceipt(.object(value), expectedRunID: run))
        }
    }
    func testUncertainEffectsRemainSettledForCleanupButCannotSoundSuccessful() throws {
        var value = completed; value["action_receipts"] = .array([unknownAction])
        let receipt = try LocalVoiceRunReceipt(.object(value), expectedRunID: run)
        XCTAssertTrue(receipt.isSettled); XCTAssertEqual(receipt.conversationReply?.unconfirmedEffects, true)
        value = completed; value["error"] = .string("incomplete_cleanup")
        XCTAssertEqual(try LocalVoiceRunReceipt(.object(value), expectedRunID: run).conversationReply?.unconfirmedEffects, true)
    }
    func testCancelledInterruptedAndFailedRetainExactSettlementWithoutInventingAnswer() throws {
        for phase in ["cancelled", "interrupted", "failed"] {
            var value = completed; value["status"] = .string(phase); value["outcome"] = .string("unknown")
            value["answer"] = .null; value["answer_source"] = .null
            let receipt = try LocalVoiceRunReceipt(.object(value), expectedRunID: run)
            XCTAssertTrue(receipt.isSettled); XCTAssertEqual(receipt.conversationReply?.status, phase); XCTAssertEqual(receipt.conversationReply?.answer, "")
        }
        var invalid = completed; invalid["status"] = .string("interrupted")
        XCTAssertThrowsError(try LocalVoiceRunReceipt(.object(invalid), expectedRunID: run))
    }
    func testMalformedOrOversizedReceiptNeverPermitsAnotherMicrophoneTurn() {
        for change: [String: RPCValue] in [["action_receipts": .array(Array(repeating: unknownAction, count: 257))], ["action_receipts": .array([.null])],
                                          ["answer": .string(String(repeating: "a", count: 32001))], ["answer": .string("hola\0")],
                                          ["error_type": .number(1)], ["error": .string(String(repeating: "x", count: 4097))]] {
            XCTAssertThrowsError(try LocalVoiceRunReceipt(.object(completed.merging(change) { _, new in new }), expectedRunID: run))
        }
    }
}
