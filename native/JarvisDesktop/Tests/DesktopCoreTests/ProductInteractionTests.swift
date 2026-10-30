import Foundation
import XCTest
@testable import DesktopCore

private enum ProductInteractionFixture {
    static let session = String(repeating: "a", count: 32)
    static let otherSession = String(repeating: "b", count: 32)
    static let run = String(repeating: "c", count: 32)
    static let questionID = String(repeating: "d", count: 32)
    static let scope = SessionScope(sessionID: session, temporary: false)
    static func question(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        let options: [RPCValue] = ["simple", "expanded"].map { id in .object([
            "id": .string(id), "label": .string(id == "simple" ? "Simple" : "Amplio"),
            "description": .string("Descripción sintética")]) }
        var value: [String: RPCValue] = ["id": .string(questionID), "kind": .string("question"),
            "scope_run_id": .string(run), "revision": .number(1), "status": .string("pending"),
            "expires_ns": .number((Date().timeIntervalSince1970 + 900) * 1_000_000_000),
            "payload": .object(["grants_tool_approval": .bool(false), "questions": .array([
                .object(["id": .string("style"), "header": .string("Estilo"),
                    "prompt": .string("¿Qué estilo prefieres?"), "allow_freeform": .bool(true),
                    "options": .array(options)])])])]
        value.merge(changes) { _, new in new }; return .object(value)
    }
    static func snapshot(session: String = ProductInteractionFixture.session,
                         run: String = ProductInteractionFixture.run, generation: Int = 0,
                         records: [RPCValue]? = nil) -> RPCValue {
        .object(["scope": .object(["session_id": .string(session), "run_id": .string(run),
            "agent_id": .string("jarvis"), "workspace_id": .string(String(repeating: "e", count: 64)),
            "temporary": .bool(false), "generation": .number(Double(generation))]),
            "interactions": .array(records ?? [question()]),
            "notification_preferences": .object(["revision": .number(0), "enabled": .bool(false), "silenced": .bool(false)])])
    }
}

private actor ProductInteractionWire {
    private var calls: [(String, [String: RPCValue])] = []
    private var readGate: CheckedContinuation<Void, Never>?
    private var actionGate: CheckedContinuation<Void, Never>?
    private var holdRead = false
    private var holdAction = false
    private var readStarted = false
    private var actionStarted = false
    private var loseAnswerACK = false
    private var generation = 0
    func holdNextRead() { holdRead = true; readStarted = false }
    func holdNextAction() { holdAction = true; actionStarted = false }
    func loseACK() { loseAnswerACK = true }
    func setGeneration(_ value: Int) { generation = value }
    func releaseRead() { readGate?.resume(); readGate = nil }
    func releaseAction() { actionGate?.resume(); actionGate = nil }
    func waitForRead() async { while !readStarted { await Task.yield() } }
    func waitForAction() async { while !actionStarted { await Task.yield() } }
    func actionCount() -> Int { calls.filter { $0.0 == "product.interaction" }.count }
    func lastAction() -> [String: RPCValue]? { calls.last { $0.0 == "product.interaction" }?.1 }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append((method, params))
        if method == "product.snapshot" {
            readStarted = true
            if holdRead { holdRead = false; await withCheckedContinuation { readGate = $0 } }
            return ProductInteractionFixture.snapshot(session: params["session_id"]?.string ?? "",
                run: params["run_id"]?.string ?? ProductInteractionFixture.run, generation: generation)
        }
        guard method == "product.interaction" else { throw RPCFailure.invalidResponse }
        actionStarted = true
        if holdAction { holdAction = false; await withCheckedContinuation { actionGate = $0 } }
        if loseAnswerACK { throw RPCFailure.disconnected }
        let args = params["arguments"] ?? .null
        if params["kind"]?.string == "question.answer" {
            return .object(["result": .object(["id": args["id"], "status": .string("answered"),
                "revision": .number(Double((args["revision"].integer ?? 0) + 1)), "grants_tool_approval": .bool(false)])])
        }
        throw RPCFailure.invalidResponse
    }
}

final class ProductInteractionTests: XCTestCase {
    func testBoundedParserBindsScopeRunAndQuestionDataWithoutAuthority() throws {
        let value = try ProductInteractionSnapshot(ProductInteractionFixture.snapshot(), expectedScope: ProductInteractionFixture.scope)
        XCTAssertEqual(value.interactions.first?.scopeRunID, ProductInteractionFixture.run)
        XCTAssertTrue(value.interactions.first?.mayAnswer == true)
        XCTAssertFalse(value.notificationPreferences.enabled)
        let prompt = try XCTUnwrap(value.interactions.first?.questions.first)
        XCTAssertTrue(ProductQuestionAnswer.option("simple").valid(for: prompt))
        XCTAssertFalse(ProductQuestionAnswer.option("unlisted").valid(for: prompt))
        XCTAssertFalse(ProductQuestionAnswer.text("").valid(for: prompt))
        XCTAssertThrowsError(try ProductInteractionSnapshot(ProductInteractionFixture.snapshot(),
            expectedScope: SessionScope(sessionID: ProductInteractionFixture.otherSession, temporary: false)))
        XCTAssertThrowsError(try ProductInteractionSnapshot(ProductInteractionFixture.snapshot(),
            expectedScope: ProductInteractionFixture.scope, expectedRunID: "different_run"))
        XCTAssertThrowsError(try ProductInteractionSnapshot(ProductInteractionFixture.snapshot(records: [
            ProductInteractionFixture.question(), ProductInteractionFixture.question()]), expectedScope: ProductInteractionFixture.scope))
    }

    func testQuestionParserRejectsSyntheticApprovalAndSensitiveInput() throws {
        let snapshot = ProductInteractionFixture.snapshot()
        let binding = try ProductInteractionBinding(snapshot["scope"], expectedScope: ProductInteractionFixture.scope)
        var row = ProductInteractionFixture.question().object!
        var payload = row["payload"]!.object!
        payload["grants_tool_approval"] = .bool(true); row["payload"] = .object(payload)
        XCTAssertThrowsError(try ProductInteractionRecord(.object(row), binding: binding))
        payload["grants_tool_approval"] = .bool(false)
        var prompt = payload["questions"]!.array![0].object!
        prompt["sensitive"] = .bool(true)
        payload["questions"] = .array([.object(prompt)]); row["payload"] = .object(payload)
        XCTAssertThrowsError(try ProductInteractionRecord(.object(row), binding: binding))
    }

    func testPanelParserRejectsMarkupRemoteSourcesAndUnsafeStyles() throws {
        let panel = ProductCanvasDefinition(widgets: [ProductCanvasWidget(id: "text_a", type: "text", title: "Resumen", text: "Texto literal")])
        XCTAssertEqual(try ProductCanvasDefinition(panel.submission).widgets.count, 1)
        var object = panel.submission.object!
        object["html"] = .string("<script>accion()</script>")
        XCTAssertThrowsError(try ProductCanvasDefinition(.object(object)))
        object.removeValue(forKey: "html")
        var widget = object["widgets"]!.array![0].object!
        widget["source_id"] = .string("https://example.invalid/other-chat")
        object["widgets"] = .array([.object(widget)])
        XCTAssertThrowsError(try ProductCanvasDefinition(.object(object)))
        widget.removeValue(forKey: "source_id"); widget["symbol"] = .string("arbitrary.symbol")
        object["widgets"] = .array([.object(widget)])
        XCTAssertThrowsError(try ProductCanvasDefinition(.object(object)))
    }

    func testDecoderHasNodeDepthAndByteBounds() {
        var deep = RPCValue.null
        for _ in 0...18 { deep = .array([deep]) }
        XCTAssertThrowsError(try ProductInteractionValidation.bounded(deep))
        XCTAssertThrowsError(try ProductInteractionValidation.bounded(.string(String(repeating: "a", count: 1_048_577))))
        XCTAssertFalse(ProductInteractionValidation.identifier("identity\n"))
    }

    func testHostScopeAndExpiredQuestionRemainDataAndResumeCannotStartActions() throws {
        let host = try ProductInteractionSnapshot(ProductInteractionFixture.snapshot(run: "host", records: []),
            expectedScope: ProductInteractionFixture.scope)
        XCTAssertEqual(host.binding.runID, "host"); XCTAssertTrue(host.interactions.isEmpty)
        let expired = try ProductInteractionRecord(ProductInteractionFixture.question(["expires_ns": .number(1)]), binding: host.binding)
        XCTAssertFalse(expired.mayAnswer)
        var result: [String: RPCValue] = ["session_id": .string(ProductInteractionFixture.session),
            "run_id": .string(ProductInteractionFixture.run), "navigation_only": .bool(true),
            "grants_tool_approval": .bool(false)]
        let navigation = try ProductNotificationNavigation(.object(result), scope: ProductInteractionFixture.scope,
            runID: ProductInteractionFixture.run)
        XCTAssertEqual(navigation.scope, ProductInteractionFixture.scope)
        result["navigation_only"] = .bool(false)
        XCTAssertThrowsError(try ProductNotificationNavigation(.object(result), scope: ProductInteractionFixture.scope,
            runID: ProductInteractionFixture.run))
    }

    @MainActor func testLateSnapshotCannotPopulateAnotherSelectedChat() async {
        let wire = ProductInteractionWire(), store = ProductInteractionStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProductInteractionFixture.scope)
        await wire.holdNextRead()
        let refreshing = Task { await store.refresh() }
        await wire.waitForRead()
        let other = SessionScope(sessionID: ProductInteractionFixture.otherSession, temporary: false)
        store.setScope(other)
        await wire.releaseRead(); await refreshing.value
        XCTAssertEqual(store.scope, other); XCTAssertNil(store.snapshot)
        XCTAssertFalse(store.isRequesting)
    }

    @MainActor func testQuestionAnswerUsesOriginalRunAndLateReturnCannotAffectNewChat() async throws {
        let wire = ProductInteractionWire(), store = ProductInteractionStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProductInteractionFixture.scope)
        await store.refresh()
        let question = try XCTUnwrap(store.pendingQuestions.first)
        await wire.holdNextAction()
        let answering = Task { await store.answer(question, answers: ["style": .option("simple")]) }
        await wire.waitForAction()
        let other = SessionScope(sessionID: ProductInteractionFixture.otherSession, temporary: false)
        store.setScope(other)
        await wire.releaseAction()
        let returned = await answering.value
        XCTAssertFalse(returned); XCTAssertEqual(store.scope, other); XCTAssertNil(store.snapshot)
        let action = await wire.lastAction()
        XCTAssertEqual(action?["session_id"], .string(ProductInteractionFixture.session))
        XCTAssertEqual(action?["run_id"], .string(ProductInteractionFixture.run))
        let stale = await store.answer(question, answers: ["style": .option("simple")])
        XCTAssertFalse(stale)
        let count = await wire.actionCount(); XCTAssertEqual(count, 1)
    }

    @MainActor func testLostAnswerACKIsNeverResentEvenAfterSwitchAndExplicitRead() async throws {
        let wire = ProductInteractionWire(), store = ProductInteractionStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProductInteractionFixture.scope); await store.refresh()
        let question = try XCTUnwrap(store.pendingQuestions.first)
        await wire.loseACK()
        let first = await store.answer(question, answers: ["style": .text("Respuesta sintética")])
        XCTAssertFalse(first); XCTAssertTrue(store.responseUnconfirmed)
        await store.refresh(); store.acknowledgeUnconfirmed()
        let second = await store.answer(question, answers: ["style": .text("Respuesta sintética")])
        XCTAssertFalse(second)
        store.setScope(SessionScope(sessionID: ProductInteractionFixture.otherSession, temporary: false))
        store.setScope(ProductInteractionFixture.scope); await store.refresh(); store.acknowledgeUnconfirmed()
        let third = await store.answer(question, answers: ["style": .option("expanded")])
        XCTAssertFalse(third)
        let count = await wire.actionCount(); XCTAssertEqual(count, 1)
    }

    @MainActor func testGenerationChangeInvalidatesExistingQuestionBeforeSubmission() async throws {
        let wire = ProductInteractionWire(), store = ProductInteractionStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProductInteractionFixture.scope); await store.refresh()
        let question = try XCTUnwrap(store.pendingQuestions.first), previousIdentity = store.viewIdentity
        await wire.setGeneration(1); await store.refresh()
        XCTAssertNotEqual(store.viewIdentity, previousIdentity)
        let returned = await store.answer(question, answers: ["style": .option("simple")])
        XCTAssertFalse(returned)
        let count = await wire.actionCount(); XCTAssertEqual(count, 0)
    }

    @MainActor func testDeferredPanelOrNotificationChoiceCannotMoveToAnotherChat() async {
        let wire = ProductInteractionWire(), store = ProductInteractionStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProductInteractionFixture.scope); await store.refresh()
        let expectedIdentity = store.viewIdentity
        store.setScope(SessionScope(sessionID: ProductInteractionFixture.otherSession, temporary: false))
        await store.refresh()
        let saved = await store.setPanel(ProductCanvasDefinition(), revision: 0, expectedIdentity: expectedIdentity)
        await store.configureNotifications(enabled: true, silenced: false, expectedIdentity: expectedIdentity)
        XCTAssertFalse(saved)
        let count = await wire.actionCount(); XCTAssertEqual(count, 0)
    }
}
