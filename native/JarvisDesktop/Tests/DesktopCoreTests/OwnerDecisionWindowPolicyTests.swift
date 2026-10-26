import AppKit
import XCTest
@testable import DesktopCore

/// No fixture is ordered, activated, made key, or placed in a modal session.
/// Only these own window objects' visibility/sheet metadata is controlled.
@MainActor private final class DecisionFixtureWindow: NSWindow {
    var fixtureVisible = true
    var fixtureSheet: NSWindow?
    override var isVisible: Bool { fixtureVisible }
    override var attachedSheet: NSWindow? { fixtureSheet }
    override var canBecomeKey: Bool { true }
    init(_ identifier: String) {
        super.init(contentRect: NSRect(x: -20_000, y: -20_000, width: 100, height: 100),
                   styleMask: [.titled], backing: .buffered, defer: true)
        self.identifier = NSUserInterfaceItemIdentifier(identifier)
        isReleasedWhenClosed = false
    }
}

final class OwnerDecisionWindowPolicyTests: XCTestCase {
    @MainActor private func fixture() -> (DecisionFixtureWindow, DecisionFixtureWindow) {
        _ = NSApplication.shared
        return (DecisionFixtureWindow(OwnerDecisionWindowPolicy.mainIdentifier),
                DecisionFixtureWindow(OwnerDecisionWindowPolicy.settingsIdentifier))
    }
    @MainActor func testDockReopenAndExplicitMainCommandKeepVisibleOwnerDecision() {
        let (main, settings) = fixture()
        for reopen in [false, true] {
            // Main can already be key or earlier in the inventory: the owner
            // card's Settings window still takes priority over opening history.
            let target = OwnerDecisionWindowPolicy.target(windows: [main, settings], modal: nil,
                key: main, awaitingOwner: true, preserveVisibleOnReopen: reopen)
            XCTAssertTrue(target === settings)
        }
    }
    @MainActor func testClosedSettingsRequestsSettingsForOwnerAndMainOnlyWithoutOwner() {
        let (main, settings) = fixture(); settings.fixtureVisible = false
        XCTAssertNil(OwnerDecisionWindowPolicy.target(windows: [settings, main], modal: nil,
            key: nil, awaitingOwner: true, preserveVisibleOnReopen: false))
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [settings, main], modal: nil,
            key: nil, awaitingOwner: false, preserveVisibleOnReopen: false) === main)
        XCTAssertNil(OwnerDecisionWindowPolicy.target(windows: [settings], modal: nil,
            key: nil, awaitingOwner: true, preserveVisibleOnReopen: false))
    }
    @MainActor func testDockVisibleFlagPreservesContextWhileOrdinaryShortcutCanShowMain() {
        let (main, settings) = fixture()
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [main, settings], modal: nil,
            key: settings, awaitingOwner: false, preserveVisibleOnReopen: true) === settings)
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [settings, main], modal: nil,
            key: settings, awaitingOwner: false, preserveVisibleOnReopen: false) === main)
    }
    @MainActor func testOwnModalAndAttachedSheetTakePriorityOverSettingsAndMain() {
        let (main, settings) = fixture(), modal = DecisionFixtureWindow("fixture.modal")
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [main, settings, modal], modal: modal,
            key: main, awaitingOwner: true, preserveVisibleOnReopen: false) === modal)
        let sheet = DecisionFixtureWindow("fixture.sheet"); settings.fixtureSheet = sheet
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [main, settings], modal: nil,
            key: main, awaitingOwner: true, preserveVisibleOnReopen: false) === sheet)
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [main, settings], modal: nil,
            key: sheet, awaitingOwner: false, preserveVisibleOnReopen: false) === sheet)
    }
    @MainActor func testUnownedOrHiddenModalDoesNotRedirectThisApp() {
        let (main, settings) = fixture(), foreign = DecisionFixtureWindow("fixture.foreign")
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [main, settings], modal: foreign,
            key: foreign, awaitingOwner: true, preserveVisibleOnReopen: false) === settings)
        foreign.fixtureVisible = false
        XCTAssertTrue(OwnerDecisionWindowPolicy.target(windows: [main, settings, foreign], modal: foreign,
            key: main, awaitingOwner: false, preserveVisibleOnReopen: false) === main)
    }
    @MainActor func testAllFourSettingsOwnersAreCoveredWithoutTreatingOtherPhasesAsApproval() {
        for position in 0..<4 {
            var phases: [String?] = [nil, "complete", "working", "failed"]
            phases[position] = "awaiting_owner"
            XCTAssertTrue(OwnerDecisionWindowPolicy.awaitingOwner(phases))
        }
        XCTAssertFalse(OwnerDecisionWindowPolicy.awaitingOwner([nil, "working", "publication_unknown", "cleanup_pending"]))
    }
}
