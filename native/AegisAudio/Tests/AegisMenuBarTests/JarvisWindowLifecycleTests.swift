import AppKit
import SwiftUI
import XCTest
@testable import AegisMenuBar

@MainActor
private final class TrackingMenu: NSMenu {
    var cancellations = 0
    override func cancelTrackingWithoutAnimation() { cancellations += 1 }
}

@MainActor
private final class FocusWindow: NSWindow {
    var focusRequests = 0
    override func makeKeyAndOrderFront(_ sender: Any?) { focusRequests += 1 }
}

@MainActor
final class JarvisWindowLifecycleTests: XCTestCase {
    func testClosingDecisionBeforeWindowRegistrationCancelsDeferredFocus() async {
        _ = NSApplication.shared
        let id = "test-approval-\(UUID())"
        RequestedWindowFocus.open(id) {}
        var dismissed = false
        RequestedWindowFocus.close(id) { dismissed = true }
        let window = FocusWindow(contentRect: .zero, styleMask: .titled, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = RequestedWindowRegistration.WindowView(id: id)
        await withCheckedContinuation { continuation in
            DispatchQueue.main.async { continuation.resume() }
        }
        XCTAssertTrue(dismissed)
        XCTAssertEqual(window.focusRequests, 0)
        window.close()
    }

    func testClosingRegisteredDecisionHidesItsWindow() async {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 100, height: 100),
                              styleMask: .titled, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let id = "test-approval-\(UUID())"
        window.contentView = RequestedWindowRegistration.WindowView(id: id)
        window.orderFront(nil)
        XCTAssertTrue(window.isVisible)
        RequestedWindowFocus.close(id) {}
        XCTAssertFalse(window.isVisible)
    }

    func testSettingsNavigationCancelsEveryPopup() async {
        let first = JarvisPopupButton(frame: .zero, pullsDown: false)
        let second = JarvisPopupButton(frame: .zero, pullsDown: false)
        let a = TrackingMenu(title: "Estilo")
        let b = TrackingMenu(title: "Voz")
        first.menu = a
        second.menu = b
        let previousA = a.cancellations
        let previousB = b.cancellations
        JarvisPopupButton.dismissMenus()
        XCTAssertGreaterThan(a.cancellations, previousA)
        XCTAssertGreaterThan(b.cancellations, previousB)
    }

    func testRemovedPickerCancelsTrackingAndDisconnectsTarget() async {
        let picker = JarvisMenuPicker(title: "Estilo", selection: .constant("direct"), options: [])
        let button = JarvisPopupButton(frame: .zero, pullsDown: false)
        let menu = TrackingMenu(title: "Estilo")
        button.menu = menu
        let coordinator = picker.makeCoordinator()
        button.target = coordinator
        let previous = menu.cancellations
        JarvisMenuPicker<String>.dismantleNSView(button, coordinator: coordinator)
        XCTAssertNil(button.target)
        XCTAssertGreaterThan(menu.cancellations, previous)
        let removed = menu.cancellations
        button.viewWillMove(toWindow: nil)
        XCTAssertGreaterThan(menu.cancellations, removed)
    }
}
