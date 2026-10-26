import Darwin
import Foundation
import XCTest
@testable import DesktopCore

final class StartupIsolationTests: XCTestCase {
    func testPlanIgnoresPythonAndLoaderEnvironmentAndPassesSourceOnlyAsArgument() throws {
        let source = "/private/tmp/own source ' quoted; not-code"
        let options = try LaunchOptions.parse(["--python", "/usr/bin/true", "--source", source,
                                              "--state", "/private/tmp/own-profile", "--codex", "/usr/bin/false"])
        let inherited = ["PATH": "/own/bin", "HOME": "/own/home", "TMPDIR": "/own/tmp", "LANG": "es_ES.UTF-8",
                         "PYTHONHOME": "/own/poison", "PYTHONPATH": "/own/other-source",
                         "PYTHONUSERBASE": "/own/slow-site", "PYTHONSTARTUP": "/own/hook",
                         "DYLD_INSERT_LIBRARIES": "/own/loader", "UNRELATED_TEST_SECRET": "fixture-only"]
        let plan = try ServiceLaunchPlan(options, inheritedEnvironment: inherited)
        XCTAssertEqual(plan.environment, ["PATH": "/own/bin", "HOME": "/own/home", "TMPDIR": "/own/tmp", "LANG": "es_ES.UTF-8"])
        XCTAssertEqual(Array(plan.arguments.prefix(5)), ["-I", "-S", "-B", "-u", "-c"])
        XCTAssertFalse(plan.arguments[5].contains(source))
        XCTAssertEqual(plan.arguments[6], source)
        XCTAssertEqual(Array(plan.arguments.suffix(4)), ["--state", options.state, "--codex", options.codex])
        XCTAssertEqual(plan.workingDirectory.path, "/")
    }

    func testSelectedMissingInterpreterDoesNotFallback() throws {
        let options = try LaunchOptions.parse(["--python", "/private/tmp/own-missing-interpreter-" + UUID().uuidString,
                                              "--source", "/own/source", "--state", "/own/profile", "--codex", "/usr/bin/false"])
        XCTAssertThrowsError(try ServiceLaunchPlan(options)) { XCTAssertEqual($0 as? RPCFailure, .configuration) }
    }

    @MainActor
    func testActualServiceCanonicalInterpreterAvoidsBlockingVenvConfigBeforeHealth() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para la comprobación de proceso real.")
        }
        let root = "/private/tmp/jarvis-isolated-" + UUID().uuidString.prefix(8)
        let fm = FileManager.default
        try fm.createDirectory(atPath: root + "/venv/bin", withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        defer { try? fm.removeItem(atPath: root) }
        let canonical = URL(fileURLWithPath: python).resolvingSymlinksInPath().path
        try fm.createSymbolicLink(atPath: root + "/venv/bin/python", withDestinationPath: canonical)
        XCTAssertEqual(mkfifo(root + "/venv/pyvenv.cfg", 0o600), 0)
        let options = try LaunchOptions.parse(["--python", root + "/venv/bin/python", "--source", source,
                                              "--state", root + "/profile", "--codex", "/usr/bin/false"])
        let plan = try ServiceLaunchPlan(options)
        XCTAssertEqual(plan.executable.path, canonical)
        let service = ServiceProcess()
        let started = ProcessInfo.processInfo.systemUptime
        try service.start(options)
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state, timeout: 5) { service.isRunning }
            let elapsed = ProcessInfo.processInfo.systemUptime - started
            let client = LocalRPCClient(stateDirectory: options.state)
            let health = try await client.call("health")
            XCTAssertEqual(health["service"].string, "jarvis")
            let sessions = try await client.call("sessions.list")
            XCTAssertEqual(sessions["sessions"].array, [])
            await client.close()
            var info = stat()
            XCTAssertEqual(lstat(root + "/venv/pyvenv.cfg", &info), 0)
            XCTAssertEqual(info.st_mode & S_IFMT, S_IFIFO)
            let stopped = await service.stop()
            XCTAssertTrue(stopped)
            print("JARVIS_STARTUP_ISOLATION canonical_fifo_health_seconds=\(elapsed)")
        } catch { _ = await service.stop(); throw error }
    }
}
