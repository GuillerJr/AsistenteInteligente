import AppKit
import Foundation
import XCTest
@testable import DesktopCore

final class WindowVisibilityMonitorTests: XCTestCase {
    @MainActor
    func testExactWindowCloseStaysHiddenAcrossRetainedViewUpdateAndReopens() {
        let center = NotificationCenter(), window = NSObject(), other = NSObject()
        var visible = true, values: [Bool] = []
        let monitor = WindowVisibilityMonitor(center: center) { values.append($0) }
        monitor.attach(to: window) { visible }
        XCTAssertEqual(values.last, true)
        center.post(name: NSWindow.willCloseNotification, object: other)
        XCTAssertEqual(values.last, true)
        // willClose arrives while AppKit can still report isVisible=true.
        center.post(name: NSWindow.willCloseNotification, object: window)
        XCTAssertEqual(values.last, false)
        monitor.attach(to: window) { visible }
        XCTAssertEqual(values.last, false)
        visible = false
        center.post(name: NSWindow.didBecomeKeyNotification, object: window)
        XCTAssertEqual(values.last, false)
        visible = true
        center.post(name: NSWindow.didBecomeKeyNotification, object: other)
        XCTAssertEqual(values.last, false)
        center.post(name: NSWindow.didBecomeKeyNotification, object: window)
        XCTAssertEqual(values.last, true)
    }

    @MainActor
    func testDetachAndOtherWindowNeverReopenOrCloseCurrentOwner() {
        let center = NotificationCenter(), first = NSObject(), second = NSObject()
        var values: [Bool] = []
        let monitor = WindowVisibilityMonitor(center: center) { values.append($0) }
        monitor.attach(to: first) { true }
        monitor.detach()
        center.post(name: NSWindow.didBecomeKeyNotification, object: first)
        XCTAssertEqual(values.last, false)
        monitor.attach(to: second) { true }
        center.post(name: NSWindow.willCloseNotification, object: first)
        XCTAssertEqual(values.last, true)
        center.post(name: NSWindow.willCloseNotification, object: second)
        XCTAssertEqual(values.last, false)
    }

    @MainActor
    func testQueuedBackgroundNotificationCannotChangeNewAttachment() async {
        let center = NotificationCenter(), old = NSObject(), current = NSObject()
        var values: [Bool] = []
        let monitor = WindowVisibilityMonitor(center: center) { values.append($0) }
        monitor.attach(to: old) { true }
        let posted = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            center.post(name: NSWindow.willCloseNotification, object: old)
            posted.signal()
        }
        // Hold this actor only until posting completed: the monitor's main-actor
        // delivery is now queued, so detach invalidates it deterministically.
        XCTAssertEqual(posted.wait(timeout: .now() + 1), .success)
        monitor.detach(); monitor.attach(to: current) { true }
        let barrier = expectation(description: "main actor queue drained")
        Task { @MainActor in barrier.fulfill() }
        await fulfillment(of: [barrier], timeout: 1)
        XCTAssertEqual(values.last, true)
        center.post(name: NSWindow.willCloseNotification, object: current)
        XCTAssertEqual(values.last, false)
    }

    @MainActor
    func testNotificationObserversAndProbeDoNotRetainWindowOrMonitor() {
        let center = NotificationCenter()
        var window: NSObject? = NSObject()
        weak var weakWindow = window
        var monitor: WindowVisibilityMonitor? = WindowVisibilityMonitor(center: center) { _ in }
        weak var weakMonitor = monitor
        monitor?.attach(to: window!) { [weak window] in window != nil }
        window = nil
        XCTAssertNil(weakWindow)
        monitor = nil
        XCTAssertNil(weakMonitor)
        center.post(name: NSWindow.willCloseNotification, object: NSObject())
    }

    @MainActor
    func testFinalWithdrawalSurvivesDeliveryOwnerBeingReleased() async {
        var delivery: WindowVisibilityDelivery? = WindowVisibilityDelivery()
        weak var weakDelivery = delivery
        let owner = UUID(), hidden = expectation(description: "final withdrawal")
        delivery?.activate(owner)
        delivery?.release(owner) { visible in
            XCTAssertFalse(visible)
            hidden.fulfill()
        }
        delivery = nil
        await fulfillment(of: [hidden], timeout: 1)
        XCTAssertNil(weakDelivery)
    }

    @MainActor
    func testOldCoordinatorWithdrawalCannotHideReplacementOrItsSelectedTab() async {
        let delivery = WindowVisibilityDelivery(), old = UUID(), replacement = UUID()
        var values: [Bool] = []
        let delivered = expectation(description: "replacement visibility")
        delivery.activate(old)
        delivery.publish(true, owner: old) { _ in XCTFail("superseded delivery") }
        delivery.activate(replacement)
        delivery.publish(true, owner: replacement) { values.append($0); delivered.fulfill() }
        delivery.release(old) { _ in XCTFail("old owner retired replacement") }
        await fulfillment(of: [delivered], timeout: 1)
        XCTAssertEqual(values, [true])
        // Tab selection belongs to SwiftUI; a hidden General tab must not pause.
        XCTAssertFalse(MaintenanceMonitorPolicy.pauses(visible: false, operation: nil,
                                                       requestInFlight: false, unresolvedResponse: false))
        XCTAssertTrue(MaintenanceMonitorPolicy.pauses(visible: false, operation: nil,
                                                      requestInFlight: false, unresolvedResponse: true))
    }
}
