import Foundation
import XCTest
@testable import DesktopCore

final class CapabilityIntegrationTests: XCTestCase {
    @MainActor
    private func withFixture(_ operation: (LocalRPCClient) async throws -> Void) async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Ejecuta script/test_desktop.sh para probar el intercambio Swift ↔ Python.")
        }
        let directory = "/private/tmp/jarvis-cap-swift-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        // This peer is a synthetic contract fixture. It performs no tool effect.
        child.arguments = ["-c", """
        import asyncio, hashlib, signal, sys
        from pathlib import Path
        from jarvis_core.protocol import LocalRpcServer, RpcError
        session='a'*32
        operation={'approval_id':'c'*32,'session_id':session,'temporary':True,
          'run_id':'b'*32,'tool_name':'workspace_write',
          'arguments_json':'{"value":9007199254740993}',
          'digest':'d'*64,'expires_at':2000000000.0}
        pending=True
        current=True
        async def approvals(params):
          if params.get('session_id')!=session or params.get('temporary') is not True:
            raise RpcError(-32012,'invalid_scope')
          return {'approvals':[operation] if pending else []}
        async def decide(params):
          global pending
          if params.get('session_id')!=session or params.get('temporary') is not True:
            raise RpcError(-32012,'invalid_scope')
          if params.get('digest')!=operation['digest']:
            raise RpcError(-32012,'approval_digest_mismatch')
          if not pending:
            return {'accepted':False}
          pending=False
          return {'accepted':True}
        async def cancel(params):
          global pending
          pending=False
          return {'accepted':True}
        async def resolve(params):
          if not current:
            raise RpcError(-32010,'stale_citation')
          return {'excerpt':'hola','verified':True}
        async def change(params):
          global current
          current=False
          return {'changed':True}
        async def main():
          done=asyncio.Event()
          asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
          async with LocalRpcServer(Path(sys.argv[1]),{
            'approvals.list':approvals,'approvals.decide':decide,
            'runs.cancel':cancel,'memory.resolve':resolve,'fixture.change':change
          },max_frame_bytes=1048576):
            await done.wait()
        asyncio.run(main())
        """, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        try startFixtureProcess(child, directory: directory)
        try await ServiceBootstrap.waitForHealth(stateDirectory: directory) { child.isRunning }
        let client = LocalRPCClient(stateDirectory: directory)
        do { try await operation(client); await client.close() }
        catch { await client.close(); throw error }
    }

    @MainActor
    func testApprovalDigestScopeAndCancelRoundTripOverRealPythonSocket() async throws {
        try await withFixture { client in
            let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
            let result = try await client.call("approvals.list", params: scope.params)
            let operation = try ApprovalOperation(try XCTUnwrap(result["approvals"].array?.first),
                                                  expectedScope: scope, expectedRunID: String(repeating: "b", count: 32))
            XCTAssertEqual(operation.argumentsJSON, "{\"value\":9007199254740993}")
            var changed = operation.decision(allow: true)
            changed["digest"] = .string(String(repeating: "e", count: 64))
            do { _ = try await client.call("approvals.decide", params: changed); XCTFail("Se aceptó un digest distinto.") }
            catch { XCTAssertEqual(error as? RPCFailure, .remote(-32012, "approval_digest_mismatch")) }
            _ = try await client.call("runs.cancel", params: scope.params)
            let late = try await client.call("approvals.decide", params: operation.decision(allow: true))
            XCTAssertEqual(late["accepted"].bool, false)
            let pending = try await client.call("approvals.list", params: scope.params)
            XCTAssertEqual(pending["approvals"].array, [])
        }
    }

    @MainActor
    func testMemoryResolutionCannotTreatChangedSnapshotAsCurrent() async throws {
        try await withFixture { client in
            let initial = try await client.call("memory.resolve", params: [:])
            XCTAssertEqual(initial["verified"].bool, true)
            XCTAssertEqual(initial["excerpt"].string, "hola")
            _ = try await client.call("fixture.change")
            do { _ = try await client.call("memory.resolve", params: [:]); XCTFail("Se aceptó una cita obsoleta.") }
            catch { XCTAssertEqual(error as? RPCFailure, .remote(-32010, "stale_citation")) }
        }
    }
}
