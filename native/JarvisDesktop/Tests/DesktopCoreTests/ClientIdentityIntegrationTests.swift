import Foundation
import XCTest
@testable import DesktopCore

final class ClientIdentityIntegrationTests: XCTestCase {
    func testTwoDistinctSignedNativeClientsPairScopeAndRevokeWithoutBearerFallback() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else { throw XCTSkip("Usa script/test_desktop.sh") }
        let root = "/private/tmp/jarvis-clients-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, signal, sys
        from pathlib import Path
        from jarvis_core.service import JarvisService
        class Peer:
            async def aclose(self): pass
            async def account_status(self): raise AssertionError('client fixture used provider')
            async def chat(self,*args,**kwargs): raise AssertionError('client fixture used inference')
        async def main():
            service=JarvisService(Path(sys.argv[1]),Peer())
            done=asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
            try:
                await service.start()
                await done.wait()
            finally: await service.aclose()
        asyncio.run(main())
        """, root + "/profile"]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try await startFixtureProcess(child, directory: root)
        try await ServiceBootstrap.waitForHealth(stateDirectory: root + "/profile") { child.isRunning }
        let owner = LocalRPCClient(stateDirectory: root + "/profile")
        let clients = ClientIdentityClient(transport: owner)
        let first = try await clients.status()
        do { _ = try await owner.call("sessions.create", params: ["title": .string(String(repeating: "X", count: 1_048_576))]); XCTFail("La petición local excesiva no debe escribirse") }
        catch { XCTAssertEqual(error as? RPCFailure, .oversized) }
        let afterOversize = try await clients.status()
        XCTAssertEqual(afterOversize, first)
        let key = try RPCClientIdentity(raw: Data(repeating: 0x29, count: 32))
        XCTAssertNotEqual(first.clientID, key.clientID)
        let review = try await clients.review(publicKey: key.publicKey, scope: "read", snapshot: first)
        let paired = try await clients.admit(review, snapshot: first)
        XCTAssertEqual(paired.revision, 2)
        let reader = LocalRPCClient(stateDirectory: root + "/profile", identity: key)
        let sessions = try await reader.call("sessions.list")
        XCTAssertEqual(sessions["sessions"].array, [])
        do { _ = try await reader.call("service.status"); XCTFail("Un lector no puede iniciar una consulta al SDK") }
        catch { guard case RPCFailure.remote(-32003, _) = error else { throw error } }
        do { _ = try await reader.call("clients.status"); XCTFail("Un lector no administra identidades") }
        catch { guard case RPCFailure.remote(-32003, _) = error else { throw error } }
        do { _ = try await reader.call("sessions.create", params: ["title": .string("No autorizado")]); XCTFail("Un cliente de lectura no puede escribir") }
        catch { guard case RPCFailure.remote(-32003, _) = error else { throw error } }
        let row = try XCTUnwrap(paired.clients.first(where: { $0.clientID == key.clientID }))
        let receipt = try await clients.revoke(row, snapshot: paired)
        XCTAssertEqual(receipt.revision, 3)
        do { _ = try await reader.call("health"); XCTFail("El cliente revocado no puede continuar en su socket abierto") }
        catch { guard case RPCFailure.remote(-32001, _) = error else { throw error } }
        let final = try await clients.status()
        XCTAssertTrue(try XCTUnwrap(final.clients.first(where: { $0.clientID == key.clientID })).revoked)
        await reader.close(); await owner.close()
    }

    func testChallengeRequiresSamePhysicalProfileAndValidNonceFields() throws {
        let root = "/private/tmp/jarvis-client-proof-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(atPath: root) }
        let key = try RPCClientIdentity(raw: Data(repeating: 0x3a, count: 32))
        let valid: RPCValue = .object(["client_id": .string(key.clientID),
            "profile_binding": .string(try RPCClientIdentity.profileBinding(root)),
            "server_id": .string(String(repeating: "a", count: 64)), "nonce": .string(String(repeating: "b", count: 64))])
        XCTAssertNoThrow(try key.validateChallenge(valid, directory: root))
        var wrong = try XCTUnwrap(valid.object); wrong["profile_binding"] = .string(String(repeating: "f", count: 64))
        XCTAssertThrowsError(try key.validateChallenge(.object(wrong), directory: root))
        wrong = try XCTUnwrap(valid.object); wrong["nonce"] = .string("not-a-nonce")
        XCTAssertThrowsError(try key.validateChallenge(.object(wrong), directory: root))
    }
}
