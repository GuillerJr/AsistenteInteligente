import Foundation
import XCTest
@testable import DesktopCore

final class SettingsSceneActionRegistrationTests: XCTestCase {
    @MainActor func testExplicitReopenUsesTheActionInstalledByTheMountedScene() {
        let registration = SettingsSceneActionRegistration(), owner = UUID()
        var openings = 0
        XCTAssertFalse(registration.perform())
        registration.install(owner: owner) { openings += 1 }
        XCTAssertTrue(registration.perform())
        XCTAssertTrue(registration.perform())
        XCTAssertEqual(openings, 2)
        registration.remove(owner: owner)
        XCTAssertFalse(registration.perform())
        XCTAssertEqual(openings, 2)
    }

    @MainActor func testRetiringAnOldSceneCannotClearItsReplacementAction() {
        let registration = SettingsSceneActionRegistration(), old = UUID(), current = UUID()
        var oldOpenings = 0, currentOpenings = 0
        registration.install(owner: old) { oldOpenings += 1 }
        registration.install(owner: current) { currentOpenings += 1 }
        registration.remove(owner: old)
        XCTAssertTrue(registration.perform())
        XCTAssertEqual(oldOpenings, 0)
        XCTAssertEqual(currentOpenings, 1)
    }

    @MainActor func testRemovingMountedSceneReleasesItsActionContext() {
        final class Context {}
        let registration = SettingsSceneActionRegistration(), owner = UUID()
        weak var released: Context?
        do {
            let context = Context()
            released = context
            registration.install(owner: owner) { _ = context }
        }
        XCTAssertNotNil(released)
        registration.remove(owner: owner)
        XCTAssertNil(released)
    }
}
