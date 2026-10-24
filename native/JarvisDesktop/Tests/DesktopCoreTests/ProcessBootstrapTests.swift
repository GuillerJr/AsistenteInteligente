import Foundation
import XCTest
@testable import DesktopCore

final class ProcessBootstrapTests: XCTestCase {
    @MainActor
    func testRealCLIReadinessSurvivesFirstImportLongerThanFiveSeconds() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Ejecuta script/test_desktop.sh para probar el arranque real del servicio.")
        }
        let directory = "/private/tmp/jarvis-boot-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let launcher = directory + "/slow-python"
        let quotedPython = "'" + python.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "'", with: "\\'") + "'"
        let script = "#!" + python + "\nimport os, sys, time\ntime.sleep(6)\nos.execv("
            + quotedPython + ", [" + quotedPython + ", *sys.argv[1:]])\n"
        try Data(script.utf8).write(to: URL(fileURLWithPath: launcher))
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: launcher)
        let options = try LaunchOptions.parse(["--python", launcher, "--source", source,
                                               "--state", directory + "/state", "--codex", "/usr/bin/false"])
        let service = ServiceProcess()
        try service.start(options)
        let start = ProcessInfo.processInfo.systemUptime
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            XCTAssertGreaterThanOrEqual(ProcessInfo.processInfo.systemUptime - start, 6)
            XCTAssertTrue(service.isRunning)
            let client = LocalRPCClient(stateDirectory: options.state)
            let health = try await client.call("health")
            XCTAssertEqual(health["protocol"].string, "jarvis-local-rpc")
            await client.close()
            let graceful = await service.stop()
            XCTAssertTrue(graceful)
            XCTAssertFalse(service.isRunning)
            XCTAssertFalse(FileManager.default.fileExists(atPath: options.state + "/service.sock"))
        } catch {
            _ = await service.stop()
            throw error
        }
    }

    @MainActor
    func testExplicitProviderStateIsForwardedWithoutReplacingSessionState() async throws {
        guard let python = ProcessInfo.processInfo.environment["JARVIS_DESKTOP_PYTHON"] else {
            throw XCTSkip("Ejecuta script/test_desktop.sh para probar el proceso sintético.")
        }
        let directory = "/private/tmp/jarvis-argv-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let launcher = directory + "/fixture-python"
        let script = "#!" + python + "\nimport json, pathlib, signal, sys\n" +
            "pathlib.Path(__file__).with_name('args.json').write_text(json.dumps(sys.argv[1:]))\nsignal.pause()\n"
        try Data(script.utf8).write(to: URL(fileURLWithPath: launcher))
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: launcher)
        let options = try LaunchOptions.parse(["--python", launcher, "--source", "/fixture-source",
                                               "--state", directory + "/new-state", "--codex", "/usr/bin/false",
                                               "--provider-state", directory + "/selected-provider"])
        let service = ServiceProcess()
        try service.start(options)
        do {
            let target = URL(fileURLWithPath: directory + "/args.json")
            for _ in 0..<100 {
                if FileManager.default.fileExists(atPath: target.path) { break }
                try await Task.sleep(for: .milliseconds(20))
            }
            let arguments = try JSONDecoder().decode([String].self, from: Data(contentsOf: target))
            XCTAssertEqual(arguments, ["-m", "jarvis_core.service", "--state", options.state,
                                       "--codex", options.codex, "--provider-state", try XCTUnwrap(options.providerState)])
            let graceful = await service.stop()
            XCTAssertTrue(graceful)
        } catch { _ = await service.stop(); throw error }
    }

    @MainActor
    func testBootstrapPreservesSpecificSecurityFailure() async throws {
        let directory = "/private/tmp/jarvis-boot-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o755])
        defer { try? FileManager.default.removeItem(atPath: directory) }
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: directory, timeout: 0.1) { true }
            XCTFail("Se aceptó un directorio no privado.")
        } catch { XCTAssertEqual(error as? RPCFailure, .insecurePath) }
    }

    @MainActor
    func testBootstrapReportsOwnedProcessExitWithoutAdoptingAnotherService() async {
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: "/private/tmp/unused") { false }
            XCTFail("Se ignoró la salida del proceso propio.")
        } catch { XCTAssertEqual(error as? RPCFailure, .serviceExited) }
    }
}
