import Foundation
import XCTest
@testable import DesktopCore

final class ConversationRefreshTests: XCTestCase {
    @MainActor
    private func withFixture(_ operation: (ConversationReader, ConversationRefresh, LocalRPCClient, SessionScope, SessionScope) async throws -> Void) async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para probar las lecturas contra el servicio real.")
        }
        let directory = "/private/tmp/jvs-refresh-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, signal, sys
        from pathlib import Path
        from uuid import uuid4
        from jarvis_core.service import JarvisService
        from jarvis_core.protocol import RpcError
        class Peer:
          calls = 0
          async def account_status(self): return {'connected': True, 'mode': 'chatgpt'}
          async def chat(self, message, *, on_thread, on_tool_call, **kwargs):
            self.calls += 1; on_thread('synthetic_' + uuid4().hex)
            await on_tool_call('workspace_write', {'path': 'external-proof.txt', 'text': 'EFECTO SINTETICO'})
            return {'answer': 'RESPUESTA EXTERNA 4'}
          async def aclose(self): pass
        async def main():
          root = Path(sys.argv[1]); peer = Peer(); service = JarvisService(root, peer)
          first = service.store.create_session('Chat seleccionado de prueba')
          other = service.store.create_session('Otro chat sintético')
          service.store.append_message(other.ref, 'user', 'CANARIO DE OTRA CONVERSACION')
          queries = []; mode = 'normal'; entered = asyncio.Event(); release = asyncio.Event()
          for name in ('runs.list', 'runs.get', 'sessions.messages'):
            handler = service.server._handlers[name]
            async def read(params, handler=handler, name=name):
              queries.append({'method': name, 'session_id': params['session_id'], 'temporary': params.get('temporary', False)})
              if name == 'sessions.messages' and params['session_id'] == first.ref.session_id:
                if mode in ('delay', 'delay-error'):
                  entered.set(); await release.wait()
                  if mode == 'delay-error': raise RpcError(-32010, 'session_not_found')
                elif mode == 'wrong-scope': return await service.messages({'session_id': other.ref.session_id})
              return await handler(params)
            service.server._handlers[name] = read
          async def info(params):
            return {'first': first.ref.session_id, 'other': other.ref.session_id, 'queries': queries,
                    'model_calls': peer.calls, 'effect_exists': (service._workspace_path / 'external-proof.txt').exists()}
          async def start_external(params):
            return await service.start_run({'session_id': first.ref.session_id, 'message': 'PREGUNTA EXTERNA DE PRUEBA'})
          async def set_mode(params):
            nonlocal mode
            mode = params['mode']; return {'ok': True}
          async def wait_entered(params):
            await asyncio.wait_for(entered.wait(), 5); return {'ok': True}
          async def unblock(params): release.set(); return {'ok': True}
          service.server._handlers.update({'fixture.info': info, 'fixture.external': start_external,
            'fixture.mode': set_mode, 'fixture.wait': wait_entered, 'fixture.release': unblock})
          done = asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
          try: await service.start(); await done.wait()
          finally: await service.aclose()
        asyncio.run(main())
        """, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        try startFixtureProcess(child, directory: directory)
        try await ServiceBootstrap.waitForHealth(stateDirectory: directory) { child.isRunning }
        let reading = LocalRPCClient(stateDirectory: directory)
        let control = LocalRPCClient(stateDirectory: directory)
        let reader = ConversationReader(transport: reading)
        let coordinator = ConversationRefresh(reader: reader)
        do {
            let info = try await control.call("fixture.info")
            let first = SessionScope(sessionID: try XCTUnwrap(info["first"].string), temporary: false)
            let other = SessionScope(sessionID: try XCTUnwrap(info["other"].string), temporary: false)
            try await operation(reader, coordinator, control, first, other)
            await reading.close(); await control.close()
        } catch { await reading.close(); await control.close(); throw error }
    }

    @MainActor
    func testExternalRunBecomesVisibleWithItsApprovalAndDurableReceiptUsingOnlyScopedReads() async throws {
        try await withFixture { reader, coordinator, control, first, _ in
            coordinator.setScope(first)
            let empty = try await coordinator.refresh()
            XCTAssertEqual(empty?.messages, [])
            _ = try await control.call("fixture.external")
            var pending = try await reader.read(first)
            for _ in 0..<100 where pending.latestRun?.status != "waiting_approval" {
                try await Task.sleep(for: .milliseconds(20)); pending = try await reader.read(first)
            }
            let run = try XCTUnwrap(pending.latestRun)
            XCTAssertEqual(run.status, "waiting_approval"); XCTAssertTrue(run.active)
            XCTAssertEqual(pending.messages.map(\.text), ["PREGUNTA EXTERNA DE PRUEBA"])
            let waiting = try await control.call("fixture.info")
            XCTAssertEqual(waiting["model_calls"].integer, 1)
            XCTAssertEqual(waiting["effect_exists"].bool, false)
            let approvals = try await control.call("approvals.list", params: first.params)
            let row = try XCTUnwrap(approvals["approvals"].array?.first)
            let approval = try ApprovalOperation(row, expectedScope: first, expectedRunID: run.id)
            // Synthetic fixture owner decision, affecting only its own temp file.
            _ = try await control.call("approvals.decide", params: approval.decision(allow: true))
            var completed = try await reader.read(first)
            for _ in 0..<100 where completed.latestRun?.status != "completed" {
                try await Task.sleep(for: .milliseconds(20)); completed = try await reader.read(first)
            }
            XCTAssertEqual(completed.latestRun?.status, "completed")
            XCTAssertEqual(completed.messages.map(\.text), ["PREGUNTA EXTERNA DE PRUEBA", "RESPUESTA EXTERNA 4"])
            XCTAssertEqual(completed.latestRun?.receipts.count, 1)
            let unchanged = try await reader.read(first)
            XCTAssertEqual(unchanged, completed)
            let trace = try await control.call("fixture.info")
            let queries = try XCTUnwrap(trace["queries"].array)
            XCTAssertTrue(queries.allSatisfy { $0["session_id"].string == first.sessionID && $0["temporary"].bool == false })
            XCTAssertTrue(queries.allSatisfy { ["runs.list", "runs.get", "sessions.messages"].contains($0["method"].string ?? "") })
            XCTAssertEqual(trace["model_calls"].integer, 1)
        }
    }

    @MainActor
    func testChangedSelectionRejectsLateSnapshotAndPreventsOverlappingReads() async throws {
        try await withFixture { _, coordinator, control, first, other in
            _ = try await control.call("fixture.mode", params: ["mode": .string("delay")])
            coordinator.setScope(first)
            let old = Task { try await coordinator.refresh() }
            _ = try await control.call("fixture.wait")
            coordinator.setScope(other)
            let overlapping = try await coordinator.refresh()
            XCTAssertNil(overlapping)
            _ = try await control.call("fixture.release")
            let stale = try await old.value
            XCTAssertNil(stale)
            let current = try await coordinator.refresh()
            XCTAssertEqual(current?.scope, other)
            XCTAssertEqual(current?.messages.map(\.text), ["CANARIO DE OTRA CONVERSACION"])
            let trace = try await control.call("fixture.info")
            XCTAssertEqual(trace["model_calls"].integer, 0)
        }
    }

    @MainActor
    func testResponseFromDifferentScopeFailsClosedBeforeAnyMessageCanBeApplied() async throws {
        try await withFixture { reader, _, control, first, _ in
            _ = try await control.call("fixture.mode", params: ["mode": .string("wrong-scope")])
            do { _ = try await reader.read(first); XCTFail("Se aceptaron mensajes de otra sesión.") }
            catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
            let trace = try await control.call("fixture.info")
            XCTAssertEqual(trace["model_calls"].integer, 0)
        }
    }

    @MainActor
    func testLateReadErrorFromRetiredSelectionIsDiscardedWithoutBreakingTheCurrentScope() async throws {
        try await withFixture { _, coordinator, control, first, other in
            _ = try await control.call("fixture.mode", params: ["mode": .string("delay-error")])
            coordinator.setScope(first)
            let old = Task { try await coordinator.refresh() }
            _ = try await control.call("fixture.wait")
            coordinator.setScope(other)
            _ = try await control.call("fixture.release")
            let discarded = try await old.value
            XCTAssertNil(discarded)
            let current = try await coordinator.refresh()
            XCTAssertEqual(current?.scope, other)
            XCTAssertEqual(current?.messages.map(\.text), ["CANARIO DE OTRA CONVERSACION"])
        }
    }
}
