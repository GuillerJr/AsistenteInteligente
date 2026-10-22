import Foundation
import XCTest
@testable import AegisMenuBar

final class JarvisDistributionTests: XCTestCase {
    @MainActor func testAboutReadsVersionBuildAndRevisionFromRelocatedBundle() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let app = directory.appendingPathComponent("Aplicaciones/Jarvis.app")
        let contents = app.appendingPathComponent("Contents")
        try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let data = try PropertyListSerialization.data(fromPropertyList: [
            "CFBundleIdentifier": "ai.aegis.distribution-test",
            "CFBundleShortVersionString": "1.0.0", "CFBundleVersion": "100",
            "AegisBuildRevision": String(repeating: "a", count: 40)
        ], format: .xml, options: 0)
        try data.write(to: contents.appendingPathComponent("Info.plist"))
        let info = JarvisBuildInfo(bundle: try XCTUnwrap(Bundle(url: app)))
        XCTAssertEqual(info.version, "1.0.0")
        XCTAssertEqual(info.build, "100")
        XCTAssertEqual(info.revision, String(repeating: "a", count: 40))
    }

    @MainActor func testLegacyLoginMigrationOnlyRecognizesRegularOwnedPlistWithExactLabel() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let plist = directory.appendingPathComponent("legacy.plist")
        func write(label: String) throws {
            try PropertyListSerialization.data(fromPropertyList: ["Label": label], format: .xml, options: 0).write(to: plist)
        }
        try write(label: "ai.aegis.menubar.autostart")
        XCTAssertTrue(JarvisLoginItemController.isLegacyAgent(plist))
        try write(label: "another.agent")
        XCTAssertFalse(JarvisLoginItemController.isLegacyAgent(plist))
        let link = directory.appendingPathComponent("linked.plist")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: plist)
        XCTAssertFalse(JarvisLoginItemController.isLegacyAgent(link))
    }
}

extension JarvisDistributionTests {
    @MainActor func testUpdateDeadlineDoesNotTerminateAnInstallerDuringSwap() async throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sleep")
        process.arguments = ["5"]
        try process.run()
        defer { if process.isRunning { process.terminate() } }
        let exited = try await JarvisUpdateController.waitForExit(process, mayTerminate: false, timeout: 0.05)
        XCTAssertFalse(exited)
        XCTAssertTrue(process.isRunning, "The installation transaction owns its shutdown and recovery")
    }
}
