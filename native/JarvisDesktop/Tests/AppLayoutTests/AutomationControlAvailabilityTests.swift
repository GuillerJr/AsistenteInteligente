import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

final class AutomationControlAvailabilityTests: XCTestCase {
    @MainActor private func makeStore() -> ChatStore {
        let store = ChatStore()
        store.ready = true; store.connected = true
        store.management.configure(ManagementClient(request: { _, _ in throw RPCFailure.unavailable }), runtimeIsActive: { true })
        return store
    }

    @MainActor func testWaitingForApprovalRetainsRevocationAndSuspendButCannotAdmitWork() async {
        let store = makeStore()
        store.activeRunID = String(repeating: "a", count: 32)
        store.runPhase = "waiting_approval"
        XCTAssertFalse(store.canChangeSession)
        let controls = store.management.automationControls(mayChangeSession: store.canChangeSession)
        XCTAssertTrue(controls.canRevoke, "An active chat must not disable stop, remove, cancel or revoke controls")
        XCTAssertTrue(controls.canSetEnabled(currentlyEnabled: true))
        XCTAssertTrue(controls.canSetSuspended(currentlySuspended: false))
        XCTAssertFalse(controls.canAdmit)
        XCTAssertFalse(controls.canSetEnabled(currentlyEnabled: false))
        XCTAssertFalse(controls.canSetSuspended(currentlySuspended: true))
        await store.management.close(); store.runtime.retire()
    }

    @MainActor func testBusyChatStillAllowsReducingAuthorityUntilManagementMutationBegins() async {
        let store = makeStore()
        store.busy = true
        XCTAssertFalse(store.canChangeSession)
        XCTAssertTrue(store.management.automationControls(mayChangeSession: store.canChangeSession).canRevoke)
        store.management.isChanging = true
        let changing = store.management.automationControls(mayChangeSession: store.canChangeSession)
        XCTAssertFalse(changing.canRevoke)
        XCTAssertFalse(changing.canAdmit)
        store.management.isChanging = false
        store.management.configure(ManagementClient(request: { _, _ in throw RPCFailure.unavailable }), runtimeIsActive: { false })
        let disconnected = store.management.automationControls(mayChangeSession: true)
        XCTAssertFalse(disconnected.canRevoke)
        XCTAssertFalse(disconnected.canAdmit)
        await store.management.close(); store.runtime.retire()
    }
}
