import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class AppGrantReceiptTests: XCTestCase {
    private func activeChat() -> (JarvisChatStore, UUID) {
        let chat = JarvisChatStore()
        chat.conversationID = UUID()
        let job = UUID()
        _ = chat.transcript.begin("Escribe texto sintético en TextEdit")
        chat.transcript.attach(job)
        return (chat, job)
    }

    private func pendingApproval(jobID: UUID) throws -> PendingApproval {
        let confirmation = try XCTUnwrap(IPCPendingConfirmation(object: [
            "call_digest": String(repeating: "a", count: 64), "tool_name": "computer_use",
            "summary": "Acción sintética", "expires_at": "2099-01-01T00:00:00Z",
            "app_grant_bundle": "com.apple.TextEdit",
        ]))
        return PendingApproval(jobID: jobID, confirmation: confirmation)
    }

    private func modelWithActiveChat() -> (MenuBarModel, UUID) {
        // Construct state only: no monitoring, service startup or credential loading.
        let model = MenuBarModel()
        let job = UUID()
        model.chat.conversationID = UUID()
        _ = model.chat.transcript.begin("Solicitud sintética")
        model.chat.transcript.attach(job)
        model.activeJobID = job
        return (model, job)
    }

    func testConfirmedReceiptDistinguishesConversationFromPermanentPermission() async {
        let (chat, job) = activeChat()
        chat.recordAppGrantResult(jobID: job, scope: "conversation", confirmed: true)
        XCTAssertEqual(chat.notice, "Permiso guardado para esta conversación.")
        chat.recordAppGrantResult(jobID: job, scope: "always", confirmed: true)
        XCTAssertTrue(chat.notice?.contains("Permiso permanente guardado") == true)
        XCTAssertTrue(chat.notice?.contains("quitarlo") == true)
        XCTAssertNil(chat.error)
    }

    func testUnconfirmedScopeCannotDisplaySuccessEvenIfTheActionFinishes() async {
        let (chat, job) = activeChat()
        chat.notice = "Confirmación de una operación anterior"
        chat.recordAppGrantResult(jobID: job, scope: "conversation", confirmed: false)
        chat.transcript.finish("La acción terminó con aprobación de una sola vez", jobID: job)
        XCTAssertNil(chat.notice)
        XCTAssertTrue(chat.error?.contains("No pude confirmar") == true)
        XCTAssertTrue(chat.error?.contains("permiso") == true)
    }

    func testLateReceiptCannotChangeAnotherChatAndNewChatClearsIt() async {
        let (chat, job) = activeChat()
        chat.recordAppGrantResult(jobID: job, scope: "conversation", confirmed: true)
        chat.transcript.finish("Finalizado", jobID: job)
        chat.newTemporaryConversation()
        XCTAssertNil(chat.notice)
        for confirmed in [true, false] {
            chat.recordAppGrantResult(jobID: job, scope: "always", confirmed: confirmed)
            XCTAssertNil(chat.notice)
            XCTAssertNil(chat.error)
        }
        let newJob = UUID()
        chat.transcript.attach(newJob)
        chat.recordAppGrantResult(jobID: newJob, scope: "unsupported", confirmed: true)
        XCTAssertNil(chat.notice)
    }

    func testConsumedApprovalReportsUnconfirmedAppScopeWithoutSendingIPC() async throws {
        for scope in ["conversation", "always"] {
            for replaced in [false, true] {
                let (model, job) = modelWithActiveChat()
                let expected = try pendingApproval(jobID: job)
                let current = replaced ? try pendingApproval(jobID: UUID()) : nil
                model.pendingApproval = current
                XCTAssertNil(model.ipcSecret)

                await model.approvePending(expected: expected, appScope: scope)

                XCTAssertTrue(model.chat.error?.contains("No pude confirmar") == true)
                XCTAssertNil(model.chat.notice)
                XCTAssertEqual(model.pendingApproval, current)
                XCTAssertEqual(model.activeJobID, job)
                XCTAssertFalse(model.approvalActionInProgress)
                XCTAssertNil(model.dualAuthorizationTask)
                model.chat.transcript.finish("Acción sintética terminada", jobID: job)
                XCTAssertTrue(model.chat.error?.contains("No pude confirmar") == true)
            }
        }
    }

    func testEarlyRejectionReceiptRequiresAvailableSessionAndExpectedScope() async throws {
        for reason in ["session-unavailable", "gate-suspended", "invalid-scope",
                       "no-expected", "same-approval"] {
            let (model, job) = modelWithActiveChat()
            let expected = try pendingApproval(jobID: job)
            if reason == "session-unavailable" { model.userSessionAvailable = false }
            if reason == "gate-suspended" { model.userSessionExecutionGate.suspend() }
            if reason == "same-approval" { model.pendingApproval = expected }

            await model.approvePending(
                expected: reason == "no-expected" ? nil : expected,
                appScope: reason == "invalid-scope" ? "unsupported" : "conversation"
            )

            XCTAssertNil(model.chat.error, reason)
            XCTAssertNil(model.chat.notice, reason)
            XCTAssertEqual(model.activeJobID, job)
            XCTAssertFalse(model.approvalActionInProgress)
        }
    }

    func testConsumedApprovalReceiptCannotChangeAnotherChatThroughModel() async throws {
        let (model, job) = modelWithActiveChat()
        let expected = try pendingApproval(jobID: job)
        model.chat.transcript.finish("Finalizado", jobID: job)
        model.chat.newTemporaryConversation()
        let nextJob = UUID()
        _ = model.chat.transcript.begin("Otra solicitud sintética")
        model.chat.transcript.attach(nextJob)

        await model.approvePending(expected: expected, appScope: "always")

        XCTAssertNil(model.chat.error)
        XCTAssertNil(model.chat.notice)
        XCTAssertEqual(model.chat.transcript.jobID, nextJob)
        XCTAssertNil(model.pendingApproval)
    }
}
