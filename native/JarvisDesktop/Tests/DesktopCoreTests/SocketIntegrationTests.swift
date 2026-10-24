import Darwin
import Foundation
import XCTest
@testable import DesktopCore

final class SocketIntegrationTests: XCTestCase {
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
