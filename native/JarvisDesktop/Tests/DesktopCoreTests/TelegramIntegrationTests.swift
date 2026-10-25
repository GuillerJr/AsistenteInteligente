import Foundation
import XCTest
@testable import DesktopCore

final class TelegramIntegrationTests: XCTestCase {
    @MainActor
    func testReadOnlyLocalRPCNeverConnectsStartsOrPersistsTheEphemeralBotSecret() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"], let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para el contrato de socket local.")
        }
        let directory = "/private/tmp/jvs-tg-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        // This synthetic RPC fixture has no Telegram transport, network,
        // account or model. It records method names and booleans, never params.
        child.arguments = ["-c", """
        import asyncio, signal, sys
        from pathlib import Path
        from jarvis_core.protocol import LocalRpcServer, RpcError
        methods = []; connected = False; token_seen = False
        secret = '72501:SENTINEL_JARVIS_TOKEN_ONLY_IN_RAM'
        def snapshot():
          return {'connected': connected, 'running': False, 'bot_id': 72501 if connected else None,
                  'username': 'jarvis_prueba' if connected else None, 'routes': [],
                  'configuration_hash': 'b'*64, 'workspace_id': 'c'*64,
                  'pinned_workspace_id': None, 'error': None}
        async def status(params):
          methods.append('telegram.status'); return snapshot()
        async def history(params):
          methods.append('telegram.history'); return {'events': [], 'limit': 50, 'capacity': 2048}
        async def connect(params):
          global connected, token_seen
          methods.append('telegram.connect'); token_seen = params == {'token': secret}; connected = token_seen
          return snapshot()
        async def start(params):
          methods.append('telegram.start'); raise RpcError(-32035, secret)
        async def trace(params):
          return {'methods': methods, 'token_seen': token_seen}
        async def main():
          done = asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
          async with LocalRpcServer(Path(sys.argv[1]), {'telegram.status': status, 'telegram.history': history,
              'telegram.connect': connect, 'telegram.start': start, 'fixture.trace': trace}): await done.wait()
        asyncio.run(main())
        """, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        try startFixtureProcess(child, directory: directory)
        try await ServiceBootstrap.waitForHealth(stateDirectory: directory) { child.isRunning }
        let transport = LocalRPCClient(stateDirectory: directory)
        let client = TelegramClient(transport: transport)
        let initial = try await client.status()
        let initialHistory = try await client.history()
        XCTAssertFalse(initial.connected)
        XCTAssertFalse(initial.running)
        XCTAssertTrue(initialHistory.events.isEmpty)
        let before = try await transport.call("fixture.trace")
        XCTAssertEqual(before["methods"], .array([.string("telegram.status"), .string("telegram.history")]))
        XCTAssertEqual(before["token_seen"].bool, false)

        let secret = "72501:SENTINEL_JARVIS_TOKEN_ONLY_IN_RAM"
        let connected = try await client.connect(token: secret)
        XCTAssertTrue(connected.connected)
        XCTAssertFalse(connected.running)
        XCTAssertFalse(ManagementValidation.json(connected.reviewValue).contains(secret))
        do {
            _ = try await client.start(configurationHash: connected.configurationHash)
            XCTFail("El fixture devuelve un diagnóstico deliberadamente secreto.")
        } catch {
            XCTAssertFalse(TelegramPresentation.errorMessage(error).contains(secret))
        }
        let after = try await transport.call("fixture.trace")
        XCTAssertEqual(after["methods"], .array([.string("telegram.status"), .string("telegram.history"),
                                                .string("telegram.connect"), .string("telegram.start")]))
        XCTAssertEqual(after["token_seen"].bool, true)
        // Invalid data fails before writing another request. No mutation is retried.
        do { _ = try await client.connect(token: "invalid"); XCTFail("Token inválido.") }
        catch { XCTAssertEqual(error as? RPCFailure, .configuration) }
        do { _ = try await client.createRoute(chatID: -10, agentID: "jarvis"); XCTFail("Grupo no permitido.") }
        catch { XCTAssertEqual(error as? RPCFailure, .configuration) }
        let unchanged = try await transport.call("fixture.trace")
        XCTAssertEqual(unchanged, after)
        for filename in try FileManager.default.contentsOfDirectory(atPath: directory) {
            let path = directory + "/" + filename
            let attributes = try FileManager.default.attributesOfItem(atPath: path)
            if (attributes[.type] as? FileAttributeType) == .typeRegular {
                let data = try Data(contentsOf: URL(fileURLWithPath: path))
                XCTAssertNil(data.range(of: Data(secret.utf8)))
            }
        }
        await client.close()
    }
}
