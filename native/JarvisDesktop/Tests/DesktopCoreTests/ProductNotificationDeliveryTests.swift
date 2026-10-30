import Foundation
import XCTest
@testable import DesktopCore

private enum NotificationFixture {
    static let namespace = String(repeating: "f", count: 64)
    static let session = String(repeating: "a", count: 32)
    static let run = String(repeating: "b", count: 32)
    static let id = String(repeating: "c", count: 32)
    static let claim = String(repeating: "d", count: 32)
    static let scope = SessionScope(sessionID: session, temporary: false)
    static func snapshot(status: String, revision: Int, enabled: Bool,
                         sessionID: String = NotificationFixture.session, runID: String = NotificationFixture.run,
                         notificationID: String = NotificationFixture.id) -> RPCValue {
        .object(["scope": .object(["session_id": .string(sessionID), "temporary": .bool(false),
            "run_id": .string(runID), "agent_id": .string("jarvis"),
            "workspace_id": .string(String(repeating: "e", count: 32)), "generation": .number(1)]),
            "interactions": .array([.object(["id": .string(notificationID), "kind": .string("notification"),
                "revision": .number(Double(revision)), "status": .string(status), "scope_run_id": .string(runID),
                "expires_ns": .number((Date().timeIntervalSince1970 + 600) * 1_000_000_000),
                "payload": .object(["attention_kind": .string("run_completed"), "correlation_id": .string(runID)])])]),
            "notification_preferences": .object(["revision": .number(1), "enabled": .bool(enabled), "silenced": .bool(false)])])
    }
}

private actor NotificationWire {
    private let sessionID: String
    private let runID: String
    private let notificationID: String
    private var status = "queued"
    private var revision = 1
    private var enabled = true
    private var valid = true
    private var calls: [String] = []
    init(sessionID: String = NotificationFixture.session, runID: String = NotificationFixture.run,
         notificationID: String = NotificationFixture.id, enabled: Bool = true) {
        self.sessionID = sessionID; self.runID = runID
        self.notificationID = notificationID; self.enabled = enabled
    }
    func invalidate() { valid = false }
    func revoke() { enabled = false }
    func count(_ kind: String) -> Int { calls.filter { $0 == kind }.count }
    var currentStatus: String { status }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        if method == "product.snapshot" {
            XCTAssertEqual(params["session_id"]?.string, sessionID)
            XCTAssertEqual(params["run_id"]?.string, runID)
            return NotificationFixture.snapshot(status: status, revision: revision, enabled: enabled,
                                                sessionID: sessionID, runID: runID, notificationID: notificationID)
        }
        guard method == "product.interaction", let kind = params["kind"]?.string else {
            throw RPCFailure.invalidResponse
        }
        calls.append(kind)
        let args = params["arguments"]!
        XCTAssertEqual(params["session_id"]?.string, sessionID)
        XCTAssertEqual(params["run_id"]?.string, runID)
        switch kind {
        case "notification.claim":
            XCTAssertEqual(args["revision"].integer, 1); revision = 2; status = "dispatching"
            return .object(["result": .object(["session_id": .string(sessionID),
                "run_id": .string(runID), "id": .string(notificationID),
                "revision": .number(2), "claim": .string(NotificationFixture.claim),
                "title": .string("Jarvis"), "body": .string("Hay una novedad en una conversación.")])])
        case "notification.validate":
            return .object(["result": .object(["id": .string(notificationID),
                "revision": .number(2), "valid": .bool(valid && enabled)])])
        case "notification.settle":
            revision += 1; status = args["outcome"].string!
            return .object(["result": .object(["id": .string(notificationID),
                "revision": .number(Double(revision)), "status": .string(status)])])
        case "notification.resume":
            revision += 1; status = "resumed"
            return .object(["result": .object(["id": .string(notificationID),
                "revision": .number(Double(revision)), "session_id": .string(sessionID),
                "run_id": .string(runID), "question_id": .null,
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
    var visible: [String: String] = [:]
    var validationObserved = false
    var addFollowedValidation = false
    var duringAdd: (@MainActor () async -> Void)?
    func requestPermission() async throws -> Bool { permissions += 1; return true }
    func canDeliver() async -> Bool { true }
    func existingIdentifiers(namespace: String) async -> [String] {
        visible.filter { $0.value == namespace }.map(\.key)
    }
    func add(identifier: String, title: String, body: String, namespace: String) async throws {
        addFollowedValidation = validationObserved
        added.append(identifier); visible[identifier] = namespace; await duringAdd?()
    }
    func remove(identifiers: [String]) {
        removed += identifiers
        for id in identifiers { visible.removeValue(forKey: id) }
    }
}

final class ProductNotificationDeliveryTests: XCTestCase {
    @MainActor private func configured(_ wire: NotificationWire, center: SyntheticNotificationCenter,
                                      scope: SessionScope = NotificationFixture.scope,
                                      runID: String = NotificationFixture.run) async -> ProductInteractionStore {
        let store = ProductInteractionStore()
        store.configure(request: { method, params in
            let result = try await wire.request(method, params)
            if params["kind"]?.string == "notification.validate", result["result"]["valid"].bool == true {
                await MainActor.run { center.validationObserved = true }
            }
            return result
        })
        store.setScope(scope, runID: runID); await store.refresh()
        return store
    }
    @MainActor func testNativeAcceptanceTextDoesNotClaimVisualDeliveryOrActivation() async throws {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let driver = ProductNotificationDelivery(center: center)
        var navigationCount = 0
        driver.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { _ in navigationCount += 1 })
        XCTAssertNil(store.notifications.first?.notificationAcceptanceText)
        await driver.process(store)
        let accepted = try XCTUnwrap(store.notifications.first)
        XCTAssertEqual(accepted.status, "delivered"); XCTAssertEqual(accepted.revision, 3)
        XCTAssertEqual(accepted.notificationAcceptanceText?.title, "Aceptado por macOS")
        XCTAssertEqual(accepted.notificationAcceptanceText?.explanation,
                       "macOS aceptó la solicitud; su visualización depende de los ajustes del sistema.")
        XCTAssertEqual(center.added.count, 1); XCTAssertEqual(center.permissions, 0)
        XCTAssertEqual(navigationCount, 0)
        let resumes = await wire.count("notification.resume"); XCTAssertEqual(resumes, 0)
        // The in-app navigation endpoint can also produce `resumed`; this is
        // deliberately not evidence that a physical macOS notification was clicked.
        _ = await store.resumeNotification(accepted)
        XCTAssertEqual(store.notifications.first?.status, "resumed")
        XCTAssertNil(store.notifications.first?.notificationAcceptanceText)
        XCTAssertEqual(navigationCount, 0)
    }
    @MainActor func testHostScopeWithoutOptInNeverRemovesAnotherChatsAcceptedRequest() async throws {
        let sessionB = String(repeating: "1", count: 32), recordB = String(repeating: "2", count: 32)
        let scopeB = SessionScope(sessionID: sessionB, temporary: false)
        let wireA = NotificationWire(runID: "host")
        let wireB = NotificationWire(sessionID: sessionB, runID: "host", notificationID: recordB, enabled: false)
        let center = SyntheticNotificationCenter()
        let storeA = await configured(wireA, center: center, runID: "host")
        let storeB = await configured(wireB, center: center, scope: scopeB, runID: "host")
        let driver = ProductNotificationDelivery(center: center)
        driver.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { _ in })
        await driver.process(storeA)
        let idA = try XCTUnwrap(center.added.first)
        XCTAssertEqual(storeA.notifications.first?.status, "delivered")
        XCTAssertEqual(storeA.notifications.first?.scopeRunID, "host")
        XCTAssertEqual(storeB.notifications.first?.scopeRunID, "host")
        XCTAssertEqual(storeB.snapshot?.notificationPreferences.enabled, false)
        await driver.process(storeB); await storeA.refresh(); await driver.process(storeA)
        XCTAssertEqual(center.visible[idA], NotificationFixture.namespace)
        XCTAssertFalse(center.removed.contains(idA))
        XCTAssertEqual(center.added, [idA]); XCTAssertEqual(center.permissions, 0)
        XCTAssertEqual(storeA.notifications.first?.status, "delivered")
        XCTAssertEqual(storeA.notifications.first?.revision, 3)
        let claimsA = await wireA.count("notification.claim"), claimsB = await wireB.count("notification.claim")
        let settlesB = await wireB.count("notification.settle"), resumesA = await wireA.count("notification.resume")
        XCTAssertEqual(claimsA, 1); XCTAssertEqual(claimsB, 0)
        XCTAssertEqual(settlesB, 0); XCTAssertEqual(resumesA, 0)
    }
    @MainActor func testValidationImmediatelyPrecedesAddAndTapOnlyNavigates() async {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let driver = ProductNotificationDelivery(center: center)
        var navigation: ProductNotificationNavigation?
        driver.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { navigation = $0 })
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
        driver.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { _ in })
        await wire.invalidate(); await driver.process(store); await driver.process(store)
        XCTAssertTrue(center.added.isEmpty)
        let claims = await wire.count("notification.claim"); XCTAssertEqual(claims, 1)
    }
    @MainActor func testPreferenceRevocationDuringNativeAddRemovesAndSettlesUnknown() async {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let driver = ProductNotificationDelivery(center: center)
        driver.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { _ in })
        center.duringAdd = { await wire.revoke(); await store.refresh() }
        await driver.process(store)
        XCTAssertEqual(center.added.count, 1)
        XCTAssertTrue(center.removed.contains(center.added[0]))
        let status = await wire.currentStatus; XCTAssertEqual(status, "unknown")
        await driver.process(store); XCTAssertEqual(center.added.count, 1)
    }

    @MainActor func testNewDriverRemovesOnlyOwnProfileOrphansAndNeverRepublishesHistoricalRow() async {
        let wire = NotificationWire(), center = SyntheticNotificationCenter()
        let store = await configured(wire, center: center)
        let old = ProductNotificationDelivery(center: center)
        old.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { _ in })
        await old.process(store)
        let id = "jarvis.product.\(NotificationFixture.id)"
        XCTAssertEqual(center.added, [id])
        let otherProfile = "jarvis.product.other-profile-record"
        let foreign = "another.application.notice"
        center.visible[otherProfile] = String(repeating: "9", count: 64)
        center.visible[foreign] = NotificationFixture.namespace
        // A process crash does not execute old.close(). The new driver has no
        // nonce or routes, and must not claim/add the delivered outbox again.
        let restarted = ProductNotificationDelivery(center: center)
        restarted.configure(namespace: { NotificationFixture.namespace }, isActive: { true }, onNavigate: { _ in })
        await store.refresh(); await restarted.process(store)
        XCTAssertNil(center.visible[id]); XCTAssertNotNil(center.visible[otherProfile])
        XCTAssertNotNil(center.visible[foreign]); XCTAssertEqual(center.added.count, 1)
        let claims = await wire.count("notification.claim"); XCTAssertEqual(claims, 1)
        // Fresh metadata reconstructed only a navigation route. Revocation can
        // still remove this durable native ID after RAM-only claims were lost.
        center.visible[id] = NotificationFixture.namespace
        await wire.revoke(); await store.refresh(); await restarted.process(store)
        XCTAssertNil(center.visible[id]); XCTAssertNotNil(center.visible[otherProfile])
        XCTAssertEqual(center.added.count, 1)
    }
}
