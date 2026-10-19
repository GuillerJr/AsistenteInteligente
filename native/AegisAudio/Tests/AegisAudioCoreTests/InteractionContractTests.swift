import Foundation
import Testing
@testable import AegisAudioCore

@Test func actionPreviewPreservesMultilineContentAndRejectsPartialContracts() throws {
    let body = "Primera línea\n" + String(repeating: "Contenido completo. ", count: 50)
    let preview = try #require(IPCActionPreview(object: ["fields": [
        ["label": "Contenido", "value": body],
    ]]))
    #expect(preview.fields.first?.value == body)
    #expect(IPCActionPreview(object: ["fields": [["label": "Contenido"]]]) == nil)
    #expect(IPCActionPreview(object: ["fields": [], "complete": false]) == nil)
    #expect(IPCActionPreview(object: ["fields": Array(repeating: ["label": "Campo", "value": "Dato"], count: 33)]) == nil)
}

@Test func inputContractRejectsUnboundedAndUnparseableData() throws {
    let valid: [String: Any] = ["question": "¿A qué hora será la reunión?",
                                "request_text": "Agenda una reunión mañana",
                                "expires_at": "2026-09-16T12:05:00Z"]
    let input = try #require(IPCInputRequest(object: valid))
    #expect(input.question == "¿A qué hora será la reunión?")
    var invalid = valid
    invalid["expires_at"] = "mañana"
    #expect(IPCInputRequest(object: invalid) == nil)
    invalid = valid
    invalid["request_text"] = String(repeating: "x", count: 4_097)
    #expect(IPCInputRequest(object: invalid) == nil)
    invalid = valid
    invalid["approve"] = true
    #expect(IPCInputRequest(object: invalid) == nil)
}

@Test func invalidActionPreviewInvalidatesTheWholePendingApproval() throws {
    let response = LocalIPCResponse(requestID: UUID(), ok: true, payload: [
        "job_id": UUID().uuidString, "status": "awaiting_confirmation",
        "confirmation": ["call_digest": String(repeating: "a", count: 64),
                           "tool_name": "mail_send_message", "summary": "Enviar correo",
                           "expires_at": "2026-09-16T12:02:00Z",
                           "preview": ["fields": [["label": "Contenido"]]]],
    ], errorCode: nil)
    #expect(IPCJobStatusEvent(response: response) == nil)
}

@Test func inputPresentationWaitsForDataAndPreservesSecurityPriority() {
    let waiting = AssistantPresentation.resolve(daemon: .online, security: .intact,
                                                provider: .configured, localBrainAvailable: false,
                                                voice: .completed, inputPending: true)
    #expect(waiting.title == "Respuesta pendiente")
    let blocked = AssistantPresentation.resolve(daemon: .online, security: .compromised,
                                                provider: .configured, localBrainAvailable: false,
                                                voice: .completed, inputPending: true)
    #expect(blocked.kind == .securityBlocked)
}

@Test func approvalLabelsMatchReviewedGoogleOperationsAndNeverGuessFromDraftSubstring() {
    #expect(ActionApprovalLabel.title(toolName: "mcp_google_workspace__draft_gmail_message") == "Guardar borrador")
    #expect(ActionApprovalLabel.title(toolName: "mcp_google_workspace__manage_event", action: "create") == "Crear evento")
    #expect(ActionApprovalLabel.title(toolName: "mcp_google_workspace__manage_event", action: "update") == "Actualizar evento")
    #expect(ActionApprovalLabel.title(toolName: "mcp_google_workspace__manage_event", action: "delete") == "Aprobar una vez")
    #expect(ActionApprovalLabel.title(toolName: "mcp_unknown__list_drafts") == "Aprobar una vez")
}
