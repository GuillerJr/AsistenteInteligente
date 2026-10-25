import Darwin
import Foundation
import XCTest
@testable import DesktopCore

final class SocketIntegrationTests: XCTestCase {
    func testIdleConnectionRenewsBeforeSingleMutationAndRevalidatesPermissions() async throws {
        let (directory, child) = try await startIdleFixture(timeout: 0.1)
        defer {
            if child.isRunning { child.terminate(); child.waitUntilExit() }
            try? FileManager.default.removeItem(atPath: directory)
        }
        let client = LocalRPCClient(stateDirectory: directory, timeout: 1, connectionIdleLimit: 0.05)
        let health = try await client.call("health")
        XCTAssertEqual(health["service"].string, "jarvis")
        // The server actually expires the first descriptor while the owner could
        // be reviewing a modal dialog. Renewal happens before this new mutation.
        try await Task.sleep(for: .milliseconds(250))
        let mutation = try await client.call("fixture.mutate")
        XCTAssertEqual(mutation["count"].integer, 1)
        XCTAssertEqual(try String(contentsOfFile: directory + "/trace", encoding: .utf8),
                       "health\nfixture.mutate\n")
        XCTAssertEqual(try String(contentsOfFile: directory + "/effects", encoding: .utf8), "1")

        // A fresh connection must recheck the endpoint and credential rather
        // than reuse a previously accepted path after its permissions change.
        try FileManager.default.setAttributes([.posixPermissions: 0o644],
                                              ofItemAtPath: directory + "/transport.token")
        try await Task.sleep(for: .milliseconds(80))
        do { _ = try await client.call("fixture.mutate"); XCTFail("Se reutilizó una credencial insegura.") }
        catch { XCTAssertEqual(error as? RPCFailure, .insecurePath) }
        XCTAssertEqual(try String(contentsOfFile: directory + "/trace", encoding: .utf8),
                       "health\nfixture.mutate\n")
        XCTAssertEqual(try String(contentsOfFile: directory + "/effects", encoding: .utf8), "1")
        await client.close()
    }

    func testIdleRenewalNeverClearsAuthorizationDenialWithoutExplicitReconnect() async throws {
        let (directory, child) = try await startIdleFixture(timeout: 2)
        defer {
            if child.isRunning { child.terminate(); child.waitUntilExit() }
            try? FileManager.default.removeItem(atPath: directory)
        }
        let client = LocalRPCClient(stateDirectory: directory, timeout: 1, connectionIdleLimit: 0.5)
        let health = try await client.call("health")
        XCTAssertEqual(health["service"].string, "jarvis")
        try Data(String(repeating: "b", count: 64).utf8)
            .write(to: URL(fileURLWithPath: directory + "/transport.token"))
        let denied = RPCFailure.remote(-32001, "No se autorizó la conexión local.")
        do { _ = try await client.call("fixture.mutate"); XCTFail("Se aceptó la credencial revocada.") }
        catch { XCTAssertEqual(error as? RPCFailure, denied) }
        try await Task.sleep(for: .milliseconds(600))
        do { _ = try await client.call("fixture.mutate"); XCTFail("La renovación borró el rechazo de acceso.") }
        catch { XCTAssertEqual(error as? RPCFailure, denied) }
        XCTAssertEqual(try String(contentsOfFile: directory + "/trace", encoding: .utf8),
                       "health\nfixture.mutate\n")
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory + "/effects"))
        await client.close()
        let reconnected = try await client.call("health")
        XCTAssertEqual(reconnected["service"].string, "jarvis")
        XCTAssertEqual(try String(contentsOfFile: directory + "/trace", encoding: .utf8),
                       "health\nfixture.mutate\nhealth\n")
        await client.close()
    }

    private func startIdleFixture(timeout: TimeInterval) async throws -> (String, Process) {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Ejecuta script/test_desktop.sh para cualificar el socket Python real.")
        }
        let directory = "/private/tmp/jarvis-idle-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, json, signal, sys
        from pathlib import Path
        from jarvis_core.protocol import LocalRpcServer
        root = Path(sys.argv[1])
        count = 0
        async def mutate(params):
            global count
            count += 1
            (root / 'effects').write_text(str(count))
            return {'count': count}
        class TracedServer(LocalRpcServer):
            async def _dispatch(self, frame):
                with (root / 'trace').open('a') as log:
                    log.write(json.loads(frame)['method'] + '\\n')
                return await super()._dispatch(frame)
        async def main():
            done = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
            async with TracedServer(root, {'fixture.mutate': mutate}, idle_timeout=float(sys.argv[2])):
                await done.wait()
        asyncio.run(main())
        """, directory, String(timeout)]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        do {
            try child.run()
            // Readiness uses only our fixture's metadata, so the trace counts
            // actual client requests exactly, including rejected credentials.
            for _ in 0..<100 {
                if FileManager.default.fileExists(atPath: directory + "/service.sock") {
                    return (directory, child)
                }
                guard child.isRunning else { throw RPCFailure.unavailable }
                try await Task.sleep(for: .milliseconds(20))
            }
            throw RPCFailure.unavailable
        } catch {
            if child.isRunning { child.terminate(); child.waitUntilExit() }
            try? FileManager.default.removeItem(atPath: directory)
            throw error
        }
    }

    func testMissingSocketIsUnavailableButExistingUnsafeEndpointsStillFailClosed() async throws {
        let directory = "/private/tmp/jarvis-missing-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let tokenPath = directory + "/transport.token"
        try Data(String(repeating: "a", count: 64).utf8).write(to: URL(fileURLWithPath: tokenPath))
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tokenPath)
        let client = LocalRPCClient(stateDirectory: directory, timeout: 0.1)
        do { _ = try await client.call("health"); XCTFail("Todavía no existe un socket.") }
        catch { XCTAssertEqual(error as? RPCFailure, .unavailable) }

        let endpoint = directory + "/service.sock"
        try Data("not a socket".utf8).write(to: URL(fileURLWithPath: endpoint))
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: endpoint)
        do { _ = try await client.call("health"); XCTFail("Un archivo no puede suplantar el socket.") }
        catch { XCTAssertEqual(error as? RPCFailure, .insecurePath) }
        try FileManager.default.removeItem(atPath: endpoint)
        try FileManager.default.createSymbolicLink(atPath: endpoint, withDestinationPath: tokenPath)
        do { _ = try await client.call("health"); XCTFail("No se debe seguir un enlace del endpoint.") }
        catch { XCTAssertEqual(error as? RPCFailure, .insecurePath) }
        await client.close()
    }

    func testSwiftClientAuthenticatesAgainstRealPythonSocketAndRevocation() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Ejecuta script/test_desktop.sh para cualificar el socket Python real.")
        }
        let directory = "/private/tmp/jarvis-swift-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, signal, sys
        from pathlib import Path
        from jarvis_core.protocol import LocalRpcServer
        async def echo(params):
            if "delay" in params:
                await asyncio.sleep(params["delay"])
            return params
        async def main():
            done = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
            async with LocalRpcServer(Path(sys.argv[1]), {"echo": echo}, max_frame_bytes=1048576):
                await done.wait()
        asyncio.run(main())
        """, directory]
        var environment = env
        environment["PYTHONPATH"] = source
        child.environment = environment
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
        defer {
            if child.isRunning { child.terminate(); child.waitUntilExit() }
        }
        let client = LocalRPCClient(stateDirectory: directory)
        var health: RPCValue?
        for _ in 0..<60 {
            if !child.isRunning { XCTFail("El fixture Python terminó antes de escuchar."); break }
            do { health = try await client.call("health"); break }
            catch { try await Task.sleep(for: .milliseconds(30)) }
        }
        XCTAssertEqual(health?["service"].string, "jarvis")
        let echo = try await client.call("echo", params: ["text": .string("Prueba Swift ↔ Python")])
        XCTAssertEqual(echo["text"].string, "Prueba Swift ↔ Python")
        let slow = try await client.call("echo", params: ["delay": .number(4), "text": .string("Respuesta tardía")])
        XCTAssertEqual(slow["text"].string, "Respuesta tardía")
        // An already-connected client must lose access after credential rotation.
        try Data(String(repeating: "b", count: 64).utf8).write(to: URL(fileURLWithPath: directory + "/transport.token"))
        do { _ = try await client.call("health"); XCTFail("Se aceptó la credencial revocada.") }
        catch { XCTAssertEqual((error as? RPCFailure), .remote(-32001, "No se autorizó la conexión local.")) }
        await client.close()
        // Manual reconnection resets the same client's cached token. An auth
        // rejection by itself must never silently reload that credential.
        let resetHealth = try await client.call("health")
        XCTAssertEqual(resetHealth["service"].string, "jarvis")
        await client.close()
        let reconnected = LocalRPCClient(stateDirectory: directory)
        let newHealth = try await reconnected.call("health")
        XCTAssertEqual(newHealth["version"].integer, 1)
        await reconnected.close()
    }

    func testSymlinkStateIsRejectedBeforeConnection() async throws {
        let directory = "/private/tmp/jarvis-swift-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(atPath: directory) }
        let linked = directory + "/linked"
        try FileManager.default.createSymbolicLink(atPath: linked, withDestinationPath: directory)
        let client = LocalRPCClient(stateDirectory: linked)
        do { _ = try await client.call("health"); XCTFail("Se aceptó un directorio enlazado.") }
        catch { XCTAssertEqual(error as? RPCFailure, .insecurePath) }
        await client.close()
    }
}
