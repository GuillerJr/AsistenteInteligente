import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

@MainActor private final class NotificationPreferenceWire {
    static let sessionA = String(repeating: "a", count: 32)
    static let sessionB = String(repeating: "b", count: 32)
    private struct Preferences {
        let revision: Int
        let enabled: Bool
        let silenced: Bool
        var value: RPCValue {
            .object(["revision": .number(Double(revision)), "enabled": .bool(enabled), "silenced": .bool(silenced)])
        }
    }
    private var preferences: [String: Preferences] = [
        NotificationPreferenceWire.sessionA: Preferences(revision: 1, enabled: true, silenced: false),
        NotificationPreferenceWire.sessionB: Preferences(revision: 1, enabled: false, silenced: false)
    ]
    private var holdA = false
    private var heldRead: CheckedContinuation<Void, Never>?
    private var onReadHeld: (() -> Void)?
    private(set) var readHeld = false
    private(set) var mutations: [[String: RPCValue]] = []

    func replace(session: String, revision: Int, enabled: Bool, silenced: Bool) {
        preferences[session] = Preferences(revision: revision, enabled: enabled, silenced: silenced)
    }
    func holdNextARead(onHeld: @escaping () -> Void) {
        holdA = true; readHeld = false; onReadHeld = onHeld
    }
    func release() {
        holdA = false; readHeld = false; onReadHeld = nil
        heldRead?.resume(); heldRead = nil
    }

    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        guard let session = params["session_id"]?.string, let current = preferences[session],
              params["temporary"]?.bool == false, params["run_id"]?.string == "host" else {
            throw RPCFailure.invalidResponse
        }
        if method == "product.snapshot" {
            let response = RPCValue.object([
                "scope": .object(["session_id": .string(session), "temporary": .bool(false),
                    "run_id": .string("host"), "agent_id": .string("jarvis"),
                    "workspace_id": .string("synthetic_workspace"), "generation": .number(0)]),
                "interactions": .array([]), "notification_preferences": current.value])
            if session == Self.sessionA, holdA {
                holdA = false
                await withCheckedContinuation { continuation in
                    heldRead = continuation; readHeld = true
                    let signal = onReadHeld; onReadHeld = nil; signal?()
                }
            }
            return response
        }
        guard method == "product.interaction", params["kind"]?.string == "notifications.configure" else {
            throw RPCFailure.invalidResponse
        }
        mutations.append(params)
        let arguments = params["arguments"] ?? .null
        guard arguments["revision"].integer == current.revision,
              let enabled = arguments["enabled"].bool, let silenced = arguments["silenced"].bool else {
            throw RPCFailure.remote(-32000, "synthetic_revision_changed")
        }
        let next = Preferences(revision: current.revision + 1, enabled: enabled, silenced: silenced)
        preferences[session] = next
        return .object(["result": next.value])
    }
}

final class ProductNotificationPreferenceChoiceTests: XCTestCase {
    @MainActor private func configured(_ wire: NotificationPreferenceWire) -> ProductInteractionStore {
        let store = ProductInteractionStore()
        store.configure(request: { try await wire.request($0, $1) })
        return store
    }
    @MainActor private func select(_ session: String, store: ProductInteractionStore) async {
        store.setScope(SessionScope(sessionID: session, temporary: false), runID: "host")
        await store.refresh()
    }

    @MainActor func testPreferenceInputsRemainFrozenAcrossAThenBThenLateAThenA() async throws {
        let wire = NotificationPreferenceWire(), store = configured(wire)
        var lateRead: Task<Void, Never>?
        defer { lateRead?.cancel(); wire.release(); store.close() }
        XCTAssertNil(ProductNotificationPreferenceChoice(store: store))
        await select(NotificationPreferenceWire.sessionA, store: store)
        let initialA = try XCTUnwrap(store.snapshot)
        let choiceA = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        XCTAssertTrue(choiceA.enabled); XCTAssertFalse(choiceA.silenced); XCTAssertEqual(choiceA.revision, 1)
        let held = expectation(description: "The explicit A snapshot is retained before selecting B")
        wire.holdNextARead(onHeld: { held.fulfill() })
        lateRead = Task { await store.refresh() }
        await fulfillment(of: [held], timeout: 2)
        guard wire.readHeld else { return }
        await select(NotificationPreferenceWire.sessionB, store: store)
        let snapshotB = try XCTUnwrap(store.snapshot)
        let choiceB = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        XCTAssertEqual(snapshotB.binding.scope.sessionID, NotificationPreferenceWire.sessionB)
        XCTAssertFalse(choiceB.enabled); XCTAssertFalse(choiceB.silenced); XCTAssertEqual(choiceB.revision, 1)
        wire.release(); await lateRead?.value
        XCTAssertEqual(store.snapshot, snapshotB)
        XCTAssertFalse(try XCTUnwrap(ProductNotificationPreferenceChoice(store: store)).enabled)
        XCTAssertTrue(choiceA.enabled); XCTAssertFalse(choiceB.enabled)
        await select(NotificationPreferenceWire.sessionA, store: store)
        let returnedA = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        XCTAssertEqual(store.snapshot, initialA)
        XCTAssertTrue(returnedA.enabled); XCTAssertFalse(returnedA.silenced)
        XCTAssertNotEqual(returnedA.viewIdentity, choiceA.viewIdentity)
        XCTAssertTrue(choiceA.enabled); XCTAssertFalse(choiceB.enabled)
        await choiceA.setEnabled(false); await choiceB.setSilenced(true)
        XCTAssertEqual(store.snapshot, initialA); XCTAssertNil(store.errorMessage)
        XCTAssertTrue(wire.mutations.isEmpty)
    }

    @MainActor func testOldChatChoiceCannotSendOrSetErrorInBWhileFreshBChoiceUsesItsExactRevision() async throws {
        let wire = NotificationPreferenceWire(), store = configured(wire)
        defer { wire.release(); store.close() }
        await select(NotificationPreferenceWire.sessionA, store: store)
        let choiceA = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        await select(NotificationPreferenceWire.sessionB, store: store)
        let snapshotB = try XCTUnwrap(store.snapshot)
        let choiceB = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        await choiceA.setEnabled(false); await choiceA.setSilenced(true)
        XCTAssertTrue(wire.mutations.isEmpty)
        XCTAssertEqual(store.snapshot, snapshotB); XCTAssertNil(store.errorMessage)
        XCTAssertFalse(choiceB.enabled); XCTAssertFalse(choiceB.silenced)
        await choiceB.setEnabled(true)
        XCTAssertEqual(wire.mutations.count, 1)
        let action = try XCTUnwrap(wire.mutations.first)
        XCTAssertEqual(action["session_id"]?.string, NotificationPreferenceWire.sessionB)
        XCTAssertEqual(action["temporary"]?.bool, false); XCTAssertEqual(action["run_id"]?.string, "host")
        XCTAssertEqual(action["kind"]?.string, "notifications.configure")
        XCTAssertEqual(action["arguments"]?["revision"].integer, 1)
        XCTAssertEqual(action["arguments"]?["enabled"].bool, true)
        XCTAssertEqual(action["arguments"]?["silenced"].bool, false)
        XCTAssertEqual(store.snapshot?.notificationPreferences.revision, 2)
        XCTAssertEqual(store.snapshot?.notificationPreferences.enabled, true)
        XCTAssertEqual(store.snapshot?.notificationPreferences.silenced, false)
        XCTAssertFalse(choiceB.enabled, "The previous input remains an immutable snapshot")
        XCTAssertNil(store.errorMessage)
    }

    @MainActor func testOldRevisionInSameChatCannotOverwriteNewSilenceChoice() async throws {
        let wire = NotificationPreferenceWire(), store = configured(wire)
        defer { wire.release(); store.close() }
        await select(NotificationPreferenceWire.sessionA, store: store)
        let old = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        let identity = store.viewIdentity
        wire.replace(session: NotificationPreferenceWire.sessionA, revision: 2, enabled: true, silenced: true)
        await store.refresh()
        let current = try XCTUnwrap(store.snapshot)
        XCTAssertEqual(store.viewIdentity, identity, "Preferences revision alone does not change chat identity")
        XCTAssertEqual(current.notificationPreferences.revision, 2)
        XCTAssertTrue(current.notificationPreferences.silenced)
        XCTAssertEqual(old.revision, 1); XCTAssertFalse(old.silenced)
        await old.setEnabled(false)
        XCTAssertTrue(wire.mutations.isEmpty)
        XCTAssertEqual(store.snapshot, current)
        XCTAssertEqual(store.errorMessage, "Los avisos cambiaron. Revísalos antes de continuar.")
        await old.setSilenced(false)
        XCTAssertTrue(wire.mutations.isEmpty); XCTAssertEqual(store.snapshot, current)
        let fresh = try XCTUnwrap(ProductNotificationPreferenceChoice(store: store))
        await fresh.setSilenced(false)
        XCTAssertEqual(wire.mutations.count, 1)
        let action = try XCTUnwrap(wire.mutations.first)
        XCTAssertEqual(action["session_id"]?.string, NotificationPreferenceWire.sessionA)
        XCTAssertEqual(action["run_id"]?.string, "host")
        XCTAssertEqual(action["arguments"]?["revision"].integer, 2)
        XCTAssertEqual(action["arguments"]?["enabled"].bool, true)
        XCTAssertEqual(action["arguments"]?["silenced"].bool, false)
        XCTAssertEqual(store.snapshot?.notificationPreferences.revision, 3)
        XCTAssertEqual(store.snapshot?.notificationPreferences.enabled, true)
        XCTAssertEqual(store.snapshot?.notificationPreferences.silenced, false)
        XCTAssertNil(store.errorMessage)
    }
}
