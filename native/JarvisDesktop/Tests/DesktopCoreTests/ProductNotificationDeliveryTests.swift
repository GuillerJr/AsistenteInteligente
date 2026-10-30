import Foundation
import XCTest
@testable import DesktopCore

private enum NotificationFixture {
    static let session = String(repeating: "a", count: 32)
    static let run = String(repeating: "b", count: 32)
    static let id = String(repeating: "c", count: 32)
    static let claim = String(repeating: "d", count: 32)
    static let scope = SessionScope(sessionID: session, temporary: false)
    static func snapshot(status: String, revision: Int, enabled: Bool) -> RPCValue {
        .object(["scope": .object(["session_id": .string(session), "temporary": .bool(false),
            "run_id": .string(run), "agent_id": .string("jarvis"),
            "workspace_id": .string(String(repeating: "e", count: 32)), "generation": .number(1)]),
            "interactions": .array([.object(["id": .string(id), "kind": .string("notification"),
                "revision": .number(Double(revision)), "status": .string(status), "scope_run_id": .string(run),
                "expires_ns": .number((Date().timeIntervalSince1970 + 600) * 1_000_000_000),
                "payload": .object(["attention_kind": .string("run_completed"), "correlation_id": .string(run)])])]),
            "notification_preferences": .object(["revision": .number(1), "enabled": .bool(enabled), "silenced": .bool(false)])])
    }
}

private actor NotificationWire {
    private var status = "queued"
    private var revision = 1
    private var enabled = true
    private var valid = true
    private var calls: [String] = []
    func invalidate() { valid = false }
    func revoke() { enabled = false }
    func count(_ kind: String) -> Int { calls.filter { $0 == kind }.count }
    var currentStatus: String { status }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        if method == "product.snapshot" {
            return NotificationFixture.snapshot(status: status, revision: revision, enabled: enabled)
        }
        guard method == "product.interaction", let kind = params["kind"]?.string else {
            throw RPCFailure.invalidResponse
        }
        calls.append(kind)
        let args = params["arguments"]!
        XCTAssertEqual(params["session_id"]?.string, NotificationFixture.session)
        XCTAssertEqual(params["run_id"]?.string, NotificationFixture.run)
        switch kind {
        case "notification.claim":
            XCTAssertEqual(args["revision"].integer, 1); revision = 2; status = "dispatching"
            return .object(["result": .object(["session_id": .string(NotificationFixture.session),
                "run_id": .string(NotificationFixture.run), "id": .string(NotificationFixture.id),
                "revision": .number(2), "claim": .string(NotificationFixture.claim),
                "title": .string("Jarvis"), "body": .string("Hay una novedad en una conversación.")])])
        case "notification.validate":
            return .object(["result": .object(["id": .string(NotificationFixture.id),
                "revision": .number(2), "valid": .bool(valid && enabled)])])
        case "notification.settle":
            revision += 1; status = args["outcome"].string!
            return .object(["result": .object(["id": .string(NotificationFixture.id),
                "revision": .number(Double(revision)), "status": .string(status)])])
        case "notification.resume":
            revision += 1; status = "resumed"
            return .object(["result": .object(["id": .string(NotificationFixture.id),
                "revision": .number(Double(revision)), "session_id": .string(NotificationFixture.session),
                "run_id": .string(NotificationFixture.run), "question_id": .null,
                "navigation_only": .bool(true), "grants_tool_approval": .bool(false)])])
        default: throw RPCFailure.invalidResponse
        }
    }
}

@MainActor private final class SyntheticNotificationCenter: ProductNotificationCenter {
    var onActivation: (@MainActor (String) async -> Void)?
    var permissions = 0
    var added: [String] = []
    var removed: [String] = []
    var validationObserved = false
    var addFollowedValidation = false
    var duringAdd: (@MainActor () async -> Void)?
    func requestPermission() async throws -> Bool { permissions += 1; return true }
    func canDeliver() async -> Bool { true }
    func add(identifier: String, title: String, body: String) async throws {
        addFollowedValidation = validationObserved
        added.append(identifier); await duringAdd?()
    }
    func remove(identifiers: [String]) { removed += identifiers }
}

final class ProductNotificationDeliveryTests: XCTestCase {
    @MainActor private func configured(_ wire: NotificationWire, center: SyntheticNotificationCenter) async -> ProductInteractionStore {
        let store = ProductInteractionStore()
        store.configure(request: { method, params in
            let result = try await wire.request(method, params)
            if params["kind"]?.string == "notification.validate", result["result"]["valid"].bool == true {
                await MainActor.run { center.validationObserved = true }
            }
            return result
        })
        store.setScope(NotificationFixture.scope, runID: NotificationFixture.run); await store.refresh()
        return store
    }
    @MainActor func testValidationImmediatelyPrecedesAddAndTapOnlyNavigates() async {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let driver = ProductNotificationDelivery(center: center)
        var navigation: ProductNotificationNavigation?
        driver.configure(isActive: { true }, onNavigate: { navigation = $0 })
        await driver.process(store)
        XCTAssertEqual(center.permissions, 0, "Polling must never request macOS permission")
        XCTAssertTrue(center.addFollowedValidation); XCTAssertEqual(center.added.count, 1)
        await driver.activate(center.added[0])
        XCTAssertEqual(navigation?.scope, NotificationFixture.scope)
        let starts = await wire.count("runs.start"), approvals = await wire.count("approvals.decide")
        XCTAssertEqual(starts, 0); XCTAssertEqual(approvals, 0)
        XCTAssertTrue(center.removed.contains(center.added[0]))
    }
    @MainActor func testRevokedValidationNeverAddsAndNeverReclaims() async {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let driver = ProductNotificationDelivery(center: center)
        driver.configure(isActive: { true }, onNavigate: { _ in })
        await wire.invalidate(); await driver.process(store); await driver.process(store)
        XCTAssertTrue(center.added.isEmpty)
        let claims = await wire.count("notification.claim"); XCTAssertEqual(claims, 1)
    }
    @MainActor func testPreferenceRevocationDuringNativeAddRemovesAndSettlesUnknown() async {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let driver = ProductNotificationDelivery(center: center)
        driver.configure(isActive: { true }, onNavigate: { _ in })
        center.duringAdd = { await wire.revoke(); await store.refresh() }
        await driver.process(store)
        XCTAssertEqual(center.added.count, 1)
        XCTAssertTrue(center.removed.contains(center.added[0]))
        let status = await wire.currentStatus; XCTAssertEqual(status, "unknown")
        await driver.process(store); XCTAssertEqual(center.added.count, 1)
    }
}
