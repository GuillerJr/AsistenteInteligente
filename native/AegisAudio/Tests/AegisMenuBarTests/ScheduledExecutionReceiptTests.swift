import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class ScheduledExecutionReceiptTests: XCTestCase {
    private func response(_ payload: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: payload)
    }

    func testSettingsAndAttentionDoNotAnnounceRunsRejectedByScheduler() throws {
        let store = JarvisSettingsStore()
        let chat = JarvisChatStore()
        for reason in ["already_running", "capacity", "execution_uncertain", "unavailable", "not_found"] {
            store.message = "Éxito anterior."
            chat.notice = "Éxito anterior."
            let data = try response(["started": false, "start_reason": reason])
            let receipt = try JarvisScheduleLaunchReceipt(data: data)
            try store.applyScheduledRunResult(data, title: "Sintética")
            chat.applyAttentionRunResult(data, title: "Sintética")
            XCTAssertTrue(store.failed, reason)
            XCTAssertEqual(store.message, receipt.message(title: "Sintética"))
            XCTAssertNil(chat.notice, reason)
            XCTAssertEqual(chat.error, receipt.message(title: "Sintética"))
        }
    }

    func testAcceptedRunClearsPreviousFailureAndReportsAdmission() throws {
        let data = try response(["started": true, "start_reason": "started"])
        let store = JarvisSettingsStore()
        store.fail("Error anterior.")
        try store.applyScheduledRunResult(data, title: "Sintética")
        XCTAssertFalse(store.failed)
        XCTAssertTrue(store.message?.contains("Solicitud aceptada") == true)
        let chat = JarvisChatStore()
        chat.error = "Error anterior."
        chat.applyAttentionRunResult(data, title: "Sintética")
        XCTAssertNil(chat.error)
        XCTAssertEqual(chat.notice, store.message)
    }

    func testMalformedOrContradictoryReceiptNeverClaimsStart() throws {
        for raw in ["{}", "{\"started\":1}", "{\"started\":\"true\"}",
                    "{\"started\":true,\"start_reason\":\"capacity\"}",
                    "{\"started\":false,\"start_reason\":\"started\"}"] {
            let data = Data(raw.utf8)
            XCTAssertThrowsError(try JarvisScheduleLaunchReceipt(data: data))
            let chat = JarvisChatStore()
            chat.notice = "Éxito anterior."
            chat.applyAttentionRunResult(data, title: "Sintética")
            XCTAssertNil(chat.notice)
            XCTAssertEqual(chat.error, JarvisScheduleLaunchReceipt.unconfirmedMessage)
        }
        let unknown = try JarvisScheduleLaunchReceipt(data: response([
            "started": false, "start_reason": "future_reason",
        ]))
        XCTAssertFalse(unknown.started)
        XCTAssertTrue(unknown.message(title: "Sintética").contains("No se inició"))
    }

    func testRejectedRunKeepsEditorOpenAndNextSaveReusesSavedTaskID() throws {
        let taskID = UUID().uuidString.lowercased()
        var draft = JarvisScheduleDraft()
        draft.title = "Sintética"
        draft.prompt = "Prueba sin efectos."
        for reason in ["capacity", "already_running", "execution_uncertain"] {
            let result = try draft.applySaveResponse(response([
                "task": ["task_id": taskID, "title": draft.title],
                "started": false, "start_reason": reason,
            ]), runNow: true)
            XCTAssertFalse(result.shouldDismiss)
            XCTAssertFalse(result.requiresReconciliation)
            XCTAssertTrue(result.message?.contains("Tarea guardada") == true)
            XCTAssertEqual(draft.payload["task_id"] as? String, taskID)
        }
        let savedWithoutRun = try draft.applySaveResponse(response([
            "task": ["task_id": taskID],
        ]), runNow: false)
        XCTAssertTrue(savedWithoutRun.shouldDismiss)
        XCTAssertNil(savedWithoutRun.message)
    }

    func testMissingLaunchReceiptRetainsSavedIDWithoutClosingEditor() throws {
        let taskID = UUID().uuidString.lowercased()
        var draft = JarvisScheduleDraft()
        for launch in [[:], ["started": true, "start_reason": "capacity"]] as [[String: Any]] {
            let result = try draft.applySaveResponse(response(
                ["task": ["task_id": taskID]].merging(launch) { _, new in new }
            ), runNow: true)
            XCTAssertFalse(result.shouldDismiss)
            XCTAssertTrue(result.requiresReconciliation)
            XCTAssertEqual(draft.taskID, taskID)
            XCTAssertTrue(result.message?.contains("quedó guardada") == true)
            XCTAssertTrue(result.message?.contains("Cierra este editor") == true)
        }
    }

    func testSuccessfulRunClosesEditorAndWrongSavedIdentityIsRejected() throws {
        var draft = JarvisScheduleDraft()
        let taskID = UUID().uuidString.lowercased()
        let result = try draft.applySaveResponse(response([
            "task": ["task_id": taskID], "started": true, "start_reason": "started",
        ]), runNow: true)
        XCTAssertTrue(result.shouldDismiss)
        XCTAssertEqual(draft.taskID, taskID)
        XCTAssertThrowsError(try draft.applySaveResponse(response([
            "task": ["task_id": UUID().uuidString],
        ]), runNow: false))
        XCTAssertEqual(draft.taskID, taskID)
    }

    func testFailedIPCDoesNotAnnounceAttentionRetryOrGoalResumption() async {
        let model = MenuBarModel()
        model.ipcSecret = nil
        for action in ["run_task", "resume_goal"] {
            model.chat.notice = "Éxito anterior."
            await model.chat.resolveAttention(.init(
                id: "synthetic:\(UUID().uuidString)", title: "Prueba", reason: "",
                action: action, conversationID: nil
            ), model: model)
            XCTAssertNil(model.chat.notice)
        }
    }

    func testCancellationTimeoutAllowsDaemonToDrainBelow30Seconds() {
        for method in ["schedules.delete", "goals.stop", "goals.delete"] {
            let timeout = JarvisScheduledOperation.timeout(method, fallback: 5)
            XCTAssertGreaterThan(timeout, 27)
            XCTAssertLessThan(timeout, 30)
        }
        XCTAssertEqual(JarvisScheduledOperation.timeout("schedules.run_now", fallback: 8), 8)
        XCTAssertNotNil(JarvisScheduledOperation.errorMessage("goal_budget_exhausted"))
        XCTAssertNotNil(JarvisScheduledOperation.errorMessage("goal_cancellation_failed"))
        XCTAssertNotNil(JarvisScheduledOperation.errorMessage("schedule_cancel_pending"))
        XCTAssertNil(JarvisScheduledOperation.errorMessage("unrecognized"))
    }
}
