import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class JarvisCodexSettingsTests: XCTestCase {
    func testContractDistinguishesAccountAvailabilityAndModelCapabilities() throws {
        let status = try JSONDecoder().decode(JarvisCodexStatus.self, from: CodexSettingsRequests.payload())
        try status.validate()
        XCTAssertTrue(status.ready)
        XCTAssertEqual(status.model_id, "synthetic-text")
        XCTAssertFalse(status.models[0].images)
        XCTAssertTrue(status.models[1].images)
        for state in ["signed_out", "login_pending", "missing", "unavailable"] {
            let value = try JSONDecoder().decode(JarvisCodexStatus.self,
                from: CodexSettingsRequests.payload(state: state))
            XCTAssertFalse(value.ready)
        }
        for state in ["configured", "future_state"] {
            XCTAssertThrowsError(try JSONDecoder().decode(JarvisCodexStatus.self,
                from: CodexSettingsRequests.payload(state: state)))
        }
    }

    func testUntrustedProviderMalformedModelAndContradictoryReadinessAreRejected() throws {
        for overrides: [String: Any] in [
            ["provider": "other"], ["installed": false], ["model_id": "bad\nmodel"],
            ["models": [["id": "same", "display_name": "A", "images": true, "is_default": true],
                        ["id": "same", "display_name": "B", "images": true, "is_default": false]]],
        ] {
            var object = try XCTUnwrap(JSONSerialization.jsonObject(with: CodexSettingsRequests.payload()) as? [String: Any])
            object.merge(overrides, uniquingKeysWith: { _, new in new })
            let result = try JSONDecoder().decode(JarvisCodexStatus.self,
                from: JSONSerialization.data(withJSONObject: object))
            XCTAssertThrowsError(try result.validate())
        }
    }

    func testLoginOnlyAcceptsScopedHTTPSOpenAIURLs() {
        for url in ["https://auth.openai.com/authorize?state=synthetic", "https://chatgpt.com/auth/login"] {
            XCTAssertNotNil(JarvisCodexLoginURL.validated(url))
        }
        for url in ["http://auth.openai.com/", "https://auth.openai.com.evil.test/", "https://evil.test/",
                    "https://user:secret@auth.openai.com/", "https://auth.openai.com:8443/",
                    "javascript:alert(1)", "file:///tmp/synthetic", "https://chatgpt.com/#private"] {
            XCTAssertNil(JarvisCodexLoginURL.validated(url), url)
        }
    }

    func testConnectRequiresOwnerActionAndLeavesOpeningTheBrowserToASeparateClick() async {
        let requests = CodexSettingsRequests(state: "signed_out")
        let store = JarvisCodexSettingsStore(transport: requests.call)
        await store.refresh()
        XCTAssertTrue(store.canConnect)
        XCTAssertNil(store.loginURL)
        XCTAssertEqual(requests.calls.map(\.method), ["settings.inference.status"])
        await store.connect()
        XCTAssertEqual(store.status?.state, .login_pending)
        XCTAssertEqual(store.loginURL?.host, "auth.openai.com")
        let receipt = requests.calls.last
        XCTAssertEqual(receipt?.method, "settings.inference.connect")
        XCTAssertEqual(receipt?.payload["confirm"] as? Bool, true)
        XCTAssertFalse(requests.calls.contains { $0.payload["secret"] != nil })
    }

    func testCancellingLoginDiscardsALateConnectURLAndReceipt() async {
        let requests = CodexSettingsRequests(state: "signed_out", paused: "settings.inference.connect")
        let store = JarvisCodexSettingsStore(transport: requests.call)
        await store.refresh()
        let connect = Task { await store.connect() }
        await requests.waitUntilStarted()
        XCTAssertTrue(store.canCancel)
        await store.cancel()
        requests.finish(CodexSettingsRequests.payload(state: "login_pending", authURL: "https://auth.openai.com/late"))
        await connect.value
        XCTAssertEqual(store.status?.state, .signed_out)
        XCTAssertNil(store.loginURL)
        XCTAssertFalse(store.busy)
    }

    func testOldStatusCannotRestoreAnAccountAfterDisconnect() async {
        let requests = CodexSettingsRequests()
        let store = JarvisCodexSettingsStore(transport: requests.call)
        await store.refresh()
        requests.paused = "settings.inference.status"
        let old = Task { await store.refresh() }
        await requests.waitUntilStarted()
        await store.disconnect()
        requests.finish(CodexSettingsRequests.payload())
        await old.value
        XCTAssertEqual(store.status?.state, .signed_out)
        XCTAssertEqual(requests.calls.last(where: { $0.method == "settings.inference.disconnect" })?.payload["confirm"] as? Bool, true)
    }

    func testModelSaveUsesOnlyCatalogueIdentityAndTestsUseNoConversationContent() async {
        let requests = CodexSettingsRequests()
        let store = JarvisCodexSettingsStore(transport: requests.call)
        await store.refresh()
        await store.saveModel("not-in-catalogue")
        XCTAssertEqual(requests.calls.count, 1)
        await store.saveModel("synthetic-vision")
        XCTAssertEqual(requests.calls.last?.method, "settings.inference.save")
        XCTAssertEqual(requests.calls.last?.payload["model_id"] as? String, "synthetic-vision")
        XCTAssertEqual(store.status?.model_id, "synthetic-vision")
        await store.test()
        XCTAssertEqual(requests.calls.last?.method, "settings.inference.test")
        XCTAssertTrue(requests.calls.last?.payload.isEmpty == true)
        XCTAssertTrue(store.shouldPoll)
        XCTAssertFalse(store.canTest)
        let count = requests.calls.count
        await store.saveModel("synthetic-text")
        await store.test()
        XCTAssertEqual(requests.calls.count, count)
        requests.testState = "passed"
        await store.refresh()
        XCTAssertFalse(store.shouldPoll)
        XCTAssertFalse(store.failed)
        XCTAssertTrue(store.message?.contains("prueba de conexión") == true)
        XCTAssertTrue(requests.calls.allSatisfy { $0.timeout == 25 })
    }

    func testUnavailableOrMalformedConnectionNeverLeavesAReadyAccountOrRawErrorVisible() async {
        let requests = CodexSettingsRequests()
        let store = JarvisCodexSettingsStore(transport: requests.call)
        await store.refresh()
        requests.fail = true
        await store.refresh()
        XCTAssertNil(store.status)
        XCTAssertFalse(store.canTest)
        XCTAssertTrue(store.failed)
        XCTAssertFalse(store.message?.contains("SYNTHETIC_PRIVATE_ERROR") == true)
    }

    func testUnsafeLoginReceiptCannotExposeALink() async {
        let requests = CodexSettingsRequests(state: "signed_out")
        requests.authURL = "https://evil.test/?token=SYNTHETIC_PRIVATE_ERROR"
        let store = JarvisCodexSettingsStore(transport: requests.call)
        await store.refresh()
        await store.connect()
        XCTAssertNil(store.loginURL)
        XCTAssertTrue(store.failed)
        XCTAssertFalse(store.message?.contains("SYNTHETIC_PRIVATE_ERROR") == true)
    }

    func testTextOnlyOrRetiredSelectionRequiresExplicitCompatibleSaveBeforeTesting() async {
        for saved in ["synthetic-text", "synthetic-retired"] {
            let requests = CodexSettingsRequests()
            requests.model = saved
            let store = JarvisCodexSettingsStore(transport: requests.call)
            await store.refresh()
            XCTAssertEqual(store.status?.compatibleModels.map(\.id), ["synthetic-vision"])
            XCTAssertEqual(store.status?.model_id, saved, "Actualizar no sustituye el modelo guardado")
            XCTAssertFalse(store.status?.hasCompatibleSelection == true)
            XCTAssertTrue(store.canChangeModel)
            XCTAssertFalse(store.canTest)
            await store.saveModel("synthetic-text")
            await store.test()
            XCTAssertEqual(requests.calls.map(\.method), ["settings.inference.status"])
            await store.saveModel("synthetic-vision")
            XCTAssertEqual(requests.calls.last?.method, "settings.inference.save")
            XCTAssertEqual(requests.calls.last?.payload["model_id"] as? String, "synthetic-vision")
            XCTAssertTrue(store.status?.hasCompatibleSelection == true)
            XCTAssertTrue(store.canTest)
            await store.test()
            XCTAssertEqual(requests.calls.last?.method, "settings.inference.test")
        }
    }
}

@MainActor
private final class CodexSettingsRequests {
    struct Call { let method: String; let payload: [String: Any]; let timeout: TimeInterval }
    var calls: [Call] = []
    var state: String
    var testState = "idle"
    var model = "synthetic-text"
    var authURL = "https://auth.openai.com/authorize?state=synthetic"
    var fail = false
    var paused: String?
    private var continuation: CheckedContinuation<Data, any Error>?
    private var arrival: CheckedContinuation<Void, Never>?
    private var hasPaused = false

    init(state: String = "ready", paused: String? = nil) { self.state = state; self.paused = paused }

    static func payload(state: String = "ready", model: String = "synthetic-text", test: String = "idle", authURL: String? = nil) -> Data {
        var object: [String: Any] = [
            "provider": "codex", "state": state, "installed": state != "missing", "model_id": model,
            "models": [
                ["id": "synthetic-text", "display_name": "Modelo sintético", "images": false, "is_default": true],
                ["id": "synthetic-vision", "display_name": "Modelo visual", "images": true, "is_default": false],
            ], "last_test": ["state": test, "error_code": NSNull(), "latency_ms": NSNull()],
        ]
        if let authURL { object["authUrl"] = authURL }
        return try! JSONSerialization.data(withJSONObject: object)
    }

    func call(_ method: String, _ payload: [String: Any], _ timeout: TimeInterval) async throws -> Data {
        calls.append(.init(method: method, payload: payload, timeout: timeout))
        if fail { throw JarvisCodexSettingsError.request("SYNTHETIC_PRIVATE_ERROR") }
        if method == paused, !hasPaused {
            hasPaused = true
            return try await withCheckedThrowingContinuation {
                continuation = $0; arrival?.resume(); arrival = nil
            }
        }
        switch method {
        case "settings.inference.connect": state = "login_pending"
        case "settings.inference.disconnect", "settings.inference.cancel": state = "signed_out"
        case "settings.inference.save": model = payload["model_id"] as? String ?? model
        case "settings.inference.test": testState = "running"
        default: break
        }
        return Self.payload(state: state, model: model, test: testState,
                            authURL: method == "settings.inference.connect" ? authURL : nil)
    }

    func waitUntilStarted() async {
        if continuation == nil { await withCheckedContinuation { arrival = $0 } }
    }
    func finish(_ data: Data) { continuation?.resume(returning: data); continuation = nil }
}
