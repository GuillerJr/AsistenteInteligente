import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class JarvisWelcomeSettingsStateTests: XCTestCase {
    private func preferences(_ id: String) -> JarvisModelPreferences {
        .init(models: [.init(model_id: id)], roles: ["conversation": .init(primary: id)])
    }

    private func readyState() -> (MenuBarModel, JarvisSettingsStore) {
        let model = MenuBarModel()
        model.daemonState = .online
        model.securityState = .intact
        model.providerState = .configured
        let store = JarvisSettingsStore()
        store.snapshot = JarvisSettingsSnapshot(models: preferences("synthetic/model"),
            models_valid: true, credential: "configured", servers: [], restart_required: false,
            memory_namespace: "synthetic-test")
        return (model, store)
    }

    func testExampleLeavesPendingApprovalAndExistingConversationUntouched() async throws {
        let (model, store) = readyState()
        let conversation = UUID()
        model.chat.conversationID = conversation
        let confirmation = try XCTUnwrap(IPCPendingConfirmation(object: [
            "call_digest": String(repeating: "a", count: 64), "tool_name": "synthetic_action",
            "summary": "Acción sintética", "expires_at": "2099-01-01T00:00:00Z"
        ]))
        let approval = PendingApproval(jobID: UUID(), confirmation: confirmation)
        model.pendingApproval = approval
        var refreshed = false
        var sent = false
        let issue = await JarvisWelcomeState.sendExample(model: model, store: store,
            refresh: { refreshed = true }, send: { sent = true; return true })
        XCTAssertEqual(issue, .activity)
        XCTAssertFalse(refreshed)
        XCTAssertFalse(sent)
        XCTAssertEqual(model.pendingApproval, approval)
        XCTAssertEqual(model.chat.conversationID, conversation)
    }

    func testExampleRechecksVoiceAndSessionAfterRefreshBeforeClearingChat() async {
        for locked in [false, true] {
            let (model, store) = readyState()
            let conversation = UUID()
            model.chat.conversationID = conversation
            var sent = false
            let issue = await JarvisWelcomeState.sendExample(model: model, store: store,
                refresh: {
                    if locked { model.userSessionAvailable = false }
                    else { model.voiceState = .listening }
                }, send: { sent = true; return true })
            XCTAssertEqual(issue, locked ? .service : .activity)
            XCTAssertFalse(sent)
            XCTAssertEqual(model.chat.conversationID, conversation)
        }
    }

    func testExampleProtectsRecordingAndPendingSettings() async {
        let (model, store) = readyState()
        model.meetingRecording = true
        XCTAssertEqual(JarvisWelcomeState.exampleIssue(model: model, store: store), .activity)
        model.meetingRecording = false
        model.speakerEnrollmentState = .loading
        XCTAssertEqual(JarvisWelcomeState.exampleIssue(model: model, store: store), .activity)
        model.speakerEnrollmentState = .idle
        model.wakeWordEnrollmentState = .loading
        XCTAssertEqual(JarvisWelcomeState.exampleIssue(model: model, store: store), .activity)
        model.wakeWordEnrollmentState = .idle
        model.queuedChatMessages = ["Mensaje sintético pendiente"]
        XCTAssertEqual(JarvisWelcomeState.exampleIssue(model: model, store: store), .activity)
        model.queuedChatMessages = []
        store.snapshot = JarvisSettingsSnapshot(models: preferences("synthetic/model"),
            models_valid: true, credential: "configured", servers: [], restart_required: true,
            memory_namespace: "synthetic-test")
        var sent = false
        let issue = await JarvisWelcomeState.sendExample(model: model, store: store,
            refresh: { XCTFail("No debe aplicar cambios ni reiniciar el servicio") },
            send: { sent = true; return true })
        XCTAssertEqual(issue, .settings)
        XCTAssertFalse(sent)
    }

    func testExampleRefreshesNewCredentialAndRequiresSuccessfulSubmission() async {
        for outcome in ["rejected", "ipc-error", "interrupted", "success"] {
            let (model, store) = readyState()
            model.providerState = .unknown // The owner just supplied the first credential.
            var refreshed = false
            var sent = false
            let issue = await JarvisWelcomeState.sendExample(model: model, store: store,
                refresh: { refreshed = true; model.providerState = .configured },
                send: {
                    sent = true
                    if outcome == "rejected" { return false }
                    XCTAssertTrue(model.chat.transcript.begin(JarvisWelcomeState.example))
                    if outcome == "ipc-error" { model.chat.error = "Error sintético" }
                    model.chat.transcript.finish("Respuesta sintética", interrupted: outcome == "interrupted")
                    return true
                })
            XCTAssertTrue(refreshed)
            XCTAssertTrue(sent)
            if outcome == "success" { XCTAssertNil(issue) }
            else { XCTAssertEqual(issue, .submission) }
        }
    }

    func testConfigurationReadRecoveryUsesFailureSourceInsteadOfTranslatedText() async {
        let store = JarvisSettingsStore()
        store.fail("Un texto que no comparte ningún prefijo", source: .configurationRead)
        store.configurationReadRecovered()
        XCTAssertFalse(store.failed)
        XCTAssertNil(store.message)
        store.fail("No se pudo leer la configuración: error de otra operación")
        store.configurationReadRecovered()
        XCTAssertTrue(store.failed, "Actualizar el estado no debe ocultar el error de otra operación")
        XCTAssertNotNil(store.message)
        store.clearMessage()
        XCTAssertNil(store.failureSource)
    }

    func testModelRefreshFollowsSavedSelectionAndPreservesDivergentDraft() async {
        let store = JarvisSettingsStore()
        let original = preferences("synthetic/original")
        let refreshed = preferences("synthetic/refreshed")
        let edited = preferences("synthetic/edited")
        store.reconcileModelDraft(previous: nil, current: original)
        XCTAssertEqual(store.modelDraft, original)
        store.reconcileModelDraft(previous: original, current: refreshed)
        XCTAssertEqual(store.modelDraft, refreshed, "Un borrador sin cambios sigue al estado nuevo")
        store.modelDraft = edited
        store.message = "Modelos guardados. Un mensaje antiguo o traducido."
        store.reconcileModelDraft(previous: refreshed, current: original)
        XCTAssertEqual(store.modelDraft, edited, "Un mensaje antiguo no puede descartar ediciones")
        store.message = "Una traducción diferente"
        store.reconcileModelDraft(previous: original, current: edited)
        XCTAssertEqual(store.modelDraft, edited, "La selección guardada se reconoce por los datos")
    }
}
