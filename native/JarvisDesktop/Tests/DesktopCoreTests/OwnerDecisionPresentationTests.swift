import AppKit
import Observation
import SwiftUI
import XCTest
@testable import DesktopCore

private actor MaintenanceOwnerCardWire {
    var submitted = false
    var decisions = 0
    private var job: RPCValue {
        .object(["operation_id": .string(String(repeating: "b", count: 32)), "kind": .string("backup.create"), "phase": .string("awaiting_owner"), "stage": .null, "result": .null, "error_code": .null, "receipt": .null, "receipt_durable": .bool(false), "worker_pending": .bool(true), "cancellation_requested": .bool(false), "scope": .object(["profile_id": .string("synthetic_profile"), "generation": .number(7)]), "review_id": MaintenanceFixtures.review["review_id"], "review_digest": MaintenanceFixtures.review["review_digest"], "cleanup_owner_pending": .bool(false)])
    }
    func request(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        switch method {
        case "maintenance.status": return .object(["profile_id": .string("synthetic_profile"), "generation": .number(7), "closing": .bool(false), "operations": .array(submitted ? [job] : []), "previous_operations": .array([])])
        case "maintenance.pending": return .object(["profile_id": .string("synthetic_profile"), "generation": .number(7), "pending": .array(submitted ? [MaintenanceFixtures.approval] : [])])
        case "backup.review": return MaintenanceFixtures.review
        case "backup.create": submitted = true; return job
        case "maintenance.decide": decisions += 1; return .object(["accepted": .bool(true)])
        default: throw RPCFailure.invalidResponse
        }
    }
    func decisionCount() -> Int { decisions }
}
private final class MaintenanceCardChanges: @unchecked Sendable {
    private let lock = NSLock()
    private var changes = 0
    func changed() { lock.lock(); changes += 1; lock.unlock() }
    var count: Int { lock.lock(); defer { lock.unlock() }; return changes }
}

final class OwnerDecisionPresentationTests: XCTestCase {
    private func operation(phase: String = "awaiting_owner", error: String? = nil, worker: Bool = false, id: String = RecoveryFixture.operationID) throws -> MaintenanceOperation {
        var value = try XCTUnwrap(RecoveryFixture.operation(phase: phase).object)
        value["error_code"] = error.map(RPCValue.string) ?? .null
        value["worker_pending"] = .bool(worker)
        value["operation_id"] = .string(id)
        return try MaintenanceOperation(.object(value))
    }
    private func notice(tab: String = "recovery", phase: String = "awaiting_owner", expires: Date = Date().addingTimeInterval(120), error: String? = nil, id: String = RecoveryFixture.operationID) throws -> OwnerDecisionNotice {
        try XCTUnwrap(OwnerDecisionNotice(tab: tab, operation: operation(phase: phase, error: error, id: id), reviewID: RecoveryFixture.reviewID, expiresAt: expires))
    }
    @MainActor func testChangingToGeneralKeepsGlobalPointerAndExplicitRevealReturnsToSameOperation() throws {
        let navigation = SettingsOwnerNavigation(), runtime = UUID(), pending = try notice()
        navigation.configure(runtimeID: runtime); navigation.select("recovery")
        navigation.detect([pending], visible: true)
        XCTAssertEqual(navigation.focusID, pending.id)
        navigation.select("general"); let sequence = navigation.revealSequence
        for _ in 0..<5 { navigation.detect([pending], visible: true) }
        XCTAssertEqual(navigation.selectedTab, "general", "Polling must not override deliberate tab selection")
        XCTAssertEqual(navigation.revealSequence, sequence)
        let pointer = try XCTUnwrap(navigation.attention(in: [pending]))
        XCTAssertEqual(pointer.id, pending.id)
        navigation.reveal(pointer)
        XCTAssertEqual(navigation.selectedTab, "recovery"); XCTAssertEqual(navigation.focusID, pending.id)
        navigation.configure(runtimeID: runtime) // A recreated Settings view keeps the same owning context.
        XCTAssertEqual(navigation.selectedTab, "recovery")
        navigation.configure(runtimeID: UUID())
        XCTAssertEqual(navigation.selectedTab, "general"); XCTAssertNil(navigation.focusID)
    }
    @MainActor func testExpiryRemainsDiscoverableWithoutTicketAndCannotBeDismissedAsClosedWhileWaiting() throws {
        let navigation = SettingsOwnerNavigation(), now = Date(), expired = try notice(expires: Date().addingTimeInterval(-1))
        navigation.configure(runtimeID: UUID()); navigation.select("general")
        XCTAssertTrue(expired.expired(at: now))
        XCTAssertTrue(expired.title(at: now).contains("caducó"))
        XCTAssertTrue(expired.message(at: now).contains("ya no permite autorizar"))
        XCTAssertTrue(expired.message(at: now).contains("No se repetirá"))
        navigation.dismiss(expired)
        XCTAssertEqual(navigation.attention(in: [expired])?.id, expired.id, "A visible timeout is not proof the backend worker settled")
        let terminal = try notice(phase: "failed", error: "maintenance_review_expired")
        XCTAssertTrue(terminal.expired(at: now)); XCTAssertNotNil(navigation.attention(in: [terminal]))
        navigation.dismiss(terminal)
        XCTAssertNil(navigation.attention(in: [terminal]), "Only the local terminal notice was hidden; its operation object remains unchanged")
        XCTAssertEqual(terminal.phase, "failed")
    }
    func testAllFourOwnersHavePersistentExpiryDenialCancellationAndUnknownExplanations() throws {
        for tab in ["maintenance", "recovery", "memory-restored", "restored"] {
            let expired = try notice(tab: tab, phase: "failed", error: "maintenance_review_expired")
            XCTAssertTrue(expired.requiresAttention); XCTAssertTrue(expired.title(at: Date()).contains("caducó"))
            for phase in ["denied", "cancelled", "publication_unknown", "cleanup_pending"] {
                let state = try notice(tab: tab, phase: phase)
                XCTAssertTrue(state.requiresAttention)
                let phrase = ["denied":"no se volverá a solicitar", "cancelled":"No se repetirá", "publication_unknown":"no se reenviará", "cleanup_pending":"no repite"][phase]!
                XCTAssertTrue(state.message(at: Date()).contains(phrase), state.message(at: Date()))
            }
        }
        XCTAssertTrue(MaintenancePresentation.operationError("maintenance_review_expired")?.contains("caducó") == true)
    }
    @MainActor func testUnconfirmedSubmissionHasPointerWithoutInventingOperationOrSuccess() throws {
        let navigation = SettingsOwnerNavigation(), unknown = try XCTUnwrap(OwnerDecisionNotice(tab: "maintenance", operation: nil, reviewID: RecoveryFixture.reviewID, expiresAt: nil, unconfirmed: true))
        XCTAssertEqual(unknown.id, RecoveryFixture.reviewID); XCTAssertEqual(unknown.phase, "response_unconfirmed")
        navigation.dismiss(unknown); XCTAssertNotNil(navigation.attention(in: [unknown]))
        XCTAssertTrue(unknown.resourcesPending); XCTAssertTrue(unknown.message(at: Date()).contains("no acredita"))
        XCTAssertNil(OwnerDecisionNotice(tab: "maintenance", operation: nil, reviewID: nil, expiresAt: nil))
    }
    @MainActor func testActualHostedExpiredCardAndGlobalBannerRemainNonemptyInOwnWindow() throws {
        _ = NSApplication.shared
        let expired = try notice(expires: Date().addingTimeInterval(-1)), navigation = SettingsOwnerNavigation()
        navigation.configure(runtimeID: UUID()); navigation.select("general")
        let card = NSHostingView(rootView: OwnerDecisionStatusView(notice: expired).padding().frame(width: 700))
        let banner = NSHostingView(rootView: OwnerDecisionBanner(notice: expired, navigation: navigation).frame(width: 700))
        var openedSettings = 0
        let mainBanner = NSHostingView(rootView: OwnerDecisionBanner(notice: expired, navigation: navigation, onReveal: { openedSettings += 1 }).frame(width: 700))
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 700, height: 250), styleMask: [.titled], backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false; window.contentView = card
        defer { window.contentView = nil; window.close() }
        card.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(card.fittingSize.height, 70, "The actual production expired-state view must not collapse to an empty approval condition")
        window.contentView = banner; banner.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(banner.fittingSize.height, 40, "The global pointer remains laid out even while selectedTab is General")
        window.contentView = mainBanner; mainBanner.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(mainBanner.fittingSize.height, 40, "The main-window pointer remains visible when the Settings scene is closed")
        XCTAssertEqual(openedSettings, 0, "Laying out the pointer does not open a window or act on the owner's decision")
        XCTAssertEqual(navigation.selectedTab, "general")
        XCTAssertFalse(window.isVisible, "This fixture never orders or activates a window")
    }
    @MainActor func testMaintenanceIdenticalStatusDoesNotWithdrawAndRecreateOwnerCard() async throws {
        let wire = MaintenanceOwnerCardWire(), store = MaintenanceStore(maxPolls: 0, pause: {})
        store.configure(client: MaintenanceClient { try await wire.request($0, $1) })
        store.setVisible(UUID(), visible: true)
        await store.prepareBackup(parent: "/private/tmp/prueba-Ω", name: "jarvis-copia")
        XCTAssertNotNil(store.review)
        await store.submitReview(); await store.refresh()
        let ticket = try XCTUnwrap(store.approval), changes = MaintenanceCardChanges()
        withObservationTracking { _ = store.approval } onChange: { changes.changed() }
        await store.refresh()
        XCTAssertEqual(store.approval?.id, ticket.id)
        XCTAssertEqual(changes.count, 0)
        let decisions = await wire.decisionCount(); XCTAssertEqual(decisions, 0)
        await store.close()
    }
    @MainActor func testNewMaintenanceExpiryOutranksOlderRecoveryDenialWhenSettingsReopens() throws {
        let navigation = SettingsOwnerNavigation(), runtime = UUID()
        navigation.configure(runtimeID: runtime); navigation.select("general")
        let recoveryID = String(repeating: "a", count: 32), maintenanceID = String(repeating: "f", count: 32)
        let oldPending = try notice(id: recoveryID), oldDenied = try notice(phase: "denied", id: recoveryID)
        navigation.detect([oldPending], visible: false)
        navigation.detect([oldDenied], visible: false)
        let newPending = try notice(tab: "maintenance", id: maintenanceID)
        navigation.detect([oldDenied, newPending], visible: false)
        XCTAssertEqual(navigation.attention(in: [oldDenied, newPending])?.id, maintenanceID)
        let sequence = navigation.revealSequence
        for _ in 0..<5 { navigation.detect([oldDenied, newPending], visible: true) }
        XCTAssertEqual(navigation.selectedTab, "general"); XCTAssertEqual(navigation.revealSequence, sequence)
        let newExpired = try notice(tab: "maintenance", phase: "failed", error: "maintenance_review_expired", id: maintenanceID)
        navigation.detect([oldDenied, newExpired], visible: false)
        let reopened = try XCTUnwrap(navigation.attention(in: [oldDenied, newExpired]))
        XCTAssertEqual(reopened.id, maintenanceID, "The first terminal row must not eclipse the newer expired owner operation")
        navigation.reveal(reopened)
        XCTAssertEqual(navigation.selectedTab, "maintenance"); XCTAssertEqual(navigation.focusID, maintenanceID)
        navigation.configure(runtimeID: runtime)
        navigation.detect([newExpired, oldDenied], visible: true)
        XCTAssertEqual(navigation.attention(in: [oldDenied, newExpired])?.id, maintenanceID, "Identical/reordered polls do not refresh an older terminal notice's priority")
        navigation.dismiss(newExpired)
        XCTAssertEqual(navigation.attention(in: [oldDenied, newExpired])?.id, recoveryID)
    }
    @MainActor func testLiveCustodyOutranksNewerTerminalNoticeAndCannotBeDismissed() throws {
        let navigation = SettingsOwnerNavigation(); navigation.configure(runtimeID: UUID())
        let heldID = String(repeating: "e", count: 32), recentID = String(repeating: "f", count: 32)
        let held = try notice(tab: "maintenance", phase: "cleanup_pending", id: heldID)
        navigation.detect([held], visible: false)
        let pending = try notice(id: recentID)
        navigation.detect([held, pending], visible: false)
        let expired = try notice(phase: "failed", error: "maintenance_review_expired", id: recentID)
        navigation.detect([held, expired], visible: false)
        XCTAssertEqual(navigation.attention(in: [expired, held])?.id, heldID)
        navigation.dismiss(held)
        XCTAssertEqual(navigation.attention(in: [expired, held])?.id, heldID)
    }
}
