import Foundation
import XCTest
@testable import DesktopCore

final class MCPIntegrationTests: XCTestCase {
    @MainActor
    private func withFixture(_ operation: (MCPManagementClient, LocalRPCClient) async throws -> Void) async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"], let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Ejecuta script/test_desktop.sh para probar el contrato Swift ↔ Python.")
        }
        let directory = "/private/tmp/jarvis-mcp-swift-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        // Contract fixture only: no MCP executable, account or owner data is used.
        child.arguments = ["-c", """
        import asyncio, signal, sys
        from pathlib import Path
        from jarvis_core.protocol import LocalRpcServer, RpcError
        configured={}
        connected=set()
        calls=[]
        def snapshot(key):
          spec=configured[key]
          active=key in connected
          return dict(spec,configured=True,connected=active,tool_count=1 if active else 0,
            catalog_hash='a'*64 if active else None,error=None)
        async def listing(params):
          calls.append('list')
          return {'servers':[snapshot(key) for key in configured],
            'supported_protocols':['2025-11-25','2026-07-28']}
        async def add(params):
          calls.append('add')
          if params['server_id'] in configured:
            raise RpcError(-32020,'mcp_already_configured')
          configured[params['server_id']]=dict(params)
          return {'configured':True}
        async def connect(params):
          key=params['server_id']
          if params!=configured.get(key):
            raise RpcError(-32020,'mcp_configuration_changed')
          calls.append('connect')
          connected.add(key)
          return {'server':snapshot(key)}
        async def disconnect(params):
          calls.append('disconnect')
          connected.discard(params['server_id'])
          return {'disconnected':True}
        async def remove(params):
          calls.append('remove')
          connected.discard(params['server_id'])
          configured.pop(params['server_id'],None)
          return {'removed':True}
        async def trace(params):
          return {'calls':calls}
        async def main():
          done=asyncio.Event()
          asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
          async with LocalRpcServer(Path(sys.argv[1]),{'mcp.list':listing,'mcp.add':add,
            'mcp.connect':connect,'mcp.disconnect':disconnect,'mcp.remove':remove,'fixture.trace':trace},
            max_frame_bytes=1048576):
            await done.wait()
        asyncio.run(main())
        """, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        try startFixtureProcess(child, directory: directory)
        try await ServiceBootstrap.waitForHealth(stateDirectory: directory) { child.isRunning }
        let transport = LocalRPCClient(stateDirectory: directory)
        let client = MCPManagementClient(transport: transport)
        do { try await operation(client, transport); await client.close() }
        catch { await client.close(); throw error }
    }

    @MainActor
    func testConfiguringAndListingNeverSendConnectAndExplicitLifecycleRoundTrips() async throws {
        try await withFixture { client, transport in
            let empty = try await client.list()
            XCTAssertTrue(empty.servers.isEmpty)
            let configuration = try MCPConfiguration(serverID: "fixture", command: ["/fixture", "two words"],
                                                       protocolVersion: .modern)
            try await client.add(configuration)
            let listed = try await client.list()
            XCTAssertEqual(listed.servers.map(\.configuration), [configuration])
            XCTAssertFalse(try XCTUnwrap(listed.servers.first).connected)
            let before = try await transport.call("fixture.trace")
            XCTAssertEqual(before["calls"].array?.compactMap(\.string), ["list", "add", "list"])
            let connection = try await client.connect(configuration)
            XCTAssertTrue(connection.connected)
            XCTAssertEqual(connection.toolCount, 1)
            try await client.disconnect(configuration.serverID)
            let disconnected = try await client.list()
            XCTAssertFalse(try XCTUnwrap(disconnected.servers.first).connected)
            try await client.remove(configuration.serverID)
            let removed = try await client.list()
            XCTAssertTrue(removed.servers.isEmpty)
            let after = try await transport.call("fixture.trace")
            XCTAssertEqual(after["calls"].array?.compactMap(\.string),
                           ["list", "add", "list", "connect", "disconnect", "list", "remove", "list"])
        }
    }

    @MainActor
    func testDifferentCommandOrProtocolCannotReuseAnOlderConnectionReview() async throws {
        try await withFixture { client, transport in
            let configuration = try MCPConfiguration(serverID: "fixture", command: ["/fixture", "original"],
                                                       protocolVersion: .modern)
            try await client.add(configuration)
            for changed in [try MCPConfiguration(serverID: "fixture", command: ["/fixture", "changed"], protocolVersion: .modern),
                            try MCPConfiguration(serverID: "fixture", command: configuration.command, protocolVersion: .legacy)] {
                do { _ = try await client.connect(changed); XCTFail("La revisión no coincide con la configuración.") }
                catch { XCTAssertEqual(error as? RPCFailure, .remote(-32020, "mcp_configuration_changed")) }
            }
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(trace["calls"].array?.compactMap(\.string), ["add"])
            let listed = try await client.list()
            XCTAssertFalse(try XCTUnwrap(listed.servers.first).connected)
        }
    }
}
