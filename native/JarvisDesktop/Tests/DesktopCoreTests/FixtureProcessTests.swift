import Foundation
import XCTest

final class FixtureProcessTests: XCTestCase {
    @MainActor
    private func process(script: String) throws -> (FixtureProcess, String) {
        guard let python = ProcessInfo.processInfo.environment["JARVIS_DESKTOP_PYTHON"] else {
            throw XCTSkip("Usa script/test_desktop.sh para el fixture de proceso real.")
        }
        let directory = "/private/tmp/jarvis-fixture-stop-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", script, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        let fixture = FixtureProcess(child)
        // These two tests deliberately exercise forced shutdown. Normal service
        // fixtures use startFixtureProcess, which fails if force was necessary.
        addTeardownBlock {
            let result = await fixture.stop()
            if result != .unconfirmed { try FileManager.default.removeItem(atPath: directory) }
            else { XCTFail("No se confirmó el cierre del fixture propio; conserva \(directory).") }
        }
        try fixture.run()
        return (fixture, directory)
    }

    private func waitForMarker(_ directory: String) async throws {
        for _ in 0..<100 {
            if FileManager.default.fileExists(atPath: directory + "/ready") { return }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTFail("El fixture no llegó al marcador acotado.")
        throw NSError(domain: "JarvisFixture", code: 1)
    }

    @MainActor
    func testAlreadyExitedChildNeedsNoSignalOrBlockingFoundationWait() async throws {
        let (fixture, directory) = try process(script: "import os,pathlib,sys;pathlib.Path(sys.argv[1],'ready').write_text('ready');os._exit(0)")
        try await waitForMarker(directory)
        try await Task.sleep(for: .milliseconds(100))
        let started = ProcessInfo.processInfo.systemUptime
        let result = await fixture.stop()
        XCTAssertEqual(result, .stopped)
        XCTAssertEqual(fixture.signalsSent, 0)
        XCTAssertLessThan(ProcessInfo.processInfo.systemUptime - started, 1)
    }

    @MainActor
    func testUnresponsiveOwnedChildIsForcedWithinABoundedCleanupAndReportsThatFact() async throws {
        let (fixture, directory) = try process(script: "import pathlib,signal,sys;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(sys.argv[1],'ready').write_text('ready');signal.pause()")
        try await waitForMarker(directory)
        let started = ProcessInfo.processInfo.systemUptime
        let result = await fixture.stop(gracefulSeconds: 0.05, forcedSeconds: 0.5)
        XCTAssertEqual(result, .forced)
        XCTAssertEqual(fixture.signalsSent, 2)
        XCTAssertLessThan(ProcessInfo.processInfo.systemUptime - started, 2)
    }
}
