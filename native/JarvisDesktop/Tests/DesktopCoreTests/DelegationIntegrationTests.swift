import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Swift -> signed own Unix RPC -> real SQLite and authority. The model peer,
/// profiles, workspace, owner decisions and crash are entirely synthetic.
final class DelegationIntegrationTests: XCTestCase {
    private static let script = """
    import asyncio, json, os, signal, sys
    from pathlib import Path
    from uuid import uuid4
    from jarvis_core.service import JarvisService
    class Peer:
      def __init__(self):
        self.calls=[]; self.release=asyncio.Event(); self.cleanup_release=asyncio.Event(); self.cleaning=False
      async def account_status(self): return {'connected':True,'mode':'chatgpt'}
      async def chat(self,message,*,on_thread,on_event,on_tool_call,**options):
        self.calls.append({'message':message,'history':len(options.get('history',[])),
          'bound':options.get('thread_id') is not None,
          'delegate_offered':any(row['name']=='agents_delegate' for row in options.get('tool_specs',[]))})
        on_thread(options.get('thread_id') or 'synthetic_'+uuid4().hex)
        if message=='escribe':
          await on_tool_call('workspace_write',{'path':'coordinacion.txt','text':'JARVIS HIJO 7319'})
        elif message=='limpieza':
          try: await self.release.wait()
          finally:
            self.cleaning=True
            await self.cleanup_release.wait()
        elif message=='espera': await self.release.wait()
        return {'answer':'Reporte del modelo propio: '+message}
      async def aclose(self): self.release.set(); self.cleanup_release.set()
    async def main():
      root=Path(sys.argv[1]); peer=Peer(); service=JarvisService(root,peer)
      ids=root/'fixture-coordination-ids.json'
      if not ids.exists():
        parent=service.store.create_session('Padre propio de coordinacion').ref
        run=service.store.create_run(parent,user_text='CONTEXTO EXCLUSIVO DEL PADRE')
        service.store.finish_run(parent,run.run_id,status='completed',receipt={'action_receipts':[]},
          assistant_text='No se hereda este contexto a los hijos.')
        ids.write_text(json.dumps({'parent':parent.session_id})); ids.chmod(0o600)
      async def trace(params):
        return {'parent':json.loads(ids.read_text())['parent'],'calls':peer.calls,
          'cleaning':peer.cleaning,'active_runs':len(service._handles)}
      async def release(params): peer.release.set(); peer.cleanup_release.set(); return {'ok':True}
      async def crash(params):
        asyncio.get_running_loop().call_later(.02,os._exit,0)
        return {'fixture_only':True}
      service.server._handlers.update({'fixture.coordination.trace':trace,
        'fixture.coordination.release':release,'fixture.coordination.crash':crash})
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: peer.release.set(); peer.cleanup_release.set(); await service.aclose()
    asyncio.run(main())
    """

    @MainActor
    private func directory() throws -> String {
        let parent = "/private/tmp/jarvis-coordination-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: parent, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: parent) }
        return parent + "/profile"
    }

    @MainActor
    private func startHost(_ profile: String) async throws -> FixtureProcess {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para integrar Swift con Python.")
        }
        let logs = "/private/tmp/jarvis-coordination-child-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process(); child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", Self.script, profile]
        var paths = source
        if let dependencies = environment["JARVIS_DESKTOP_DEPENDENCIES"] { paths += ":" + dependencies }
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": paths,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice
        let fd = Darwin.open(logs + "/stderr.log", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw RPCFailure.configuration }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        child.standardError = handle
        addTeardownBlock { try? handle.close() }
        let owner = try startFixtureProcess(child, directory: logs)
        try await ServiceBootstrap.waitForHealth(stateDirectory: profile, timeout: 8) { child.isRunning }
        return owner
    }

    private func identity() -> String {
        UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }

    private func task(_ message: String) -> DelegatedTaskSpec {
        DelegatedTaskSpec(agentID: "jarvis", agentRevision: 1, message: message, maxSteps: 3)
    }

    @MainActor
    private func parent(_ transport: LocalRPCClient) async throws -> SessionScope {
        let trace = try await transport.call("fixture.coordination.trace")
        return SessionScope(sessionID: try XCTUnwrap(trace["parent"].string), temporary: false)
    }

    @MainActor
    private func final(_ client: DelegationClient, _ initial: DelegationSnapshot) async throws -> DelegationSnapshot {
        let deadline = ContinuousClock.now.advanced(by: .seconds(8))
        var value = initial
        while ContinuousClock.now < deadline {
            value = try await client.get(value)
            if value.settled { return value }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor
    func testTwoChildrenSignedAuthorityIndependentWriteAndRestartObservation() async throws {
        let profile = try directory(), firstHost = try await startHost(profile)
        let transport = LocalRPCClient(stateDirectory: profile), client = DelegationClient(transport: transport)
        let scope = try await parent(transport), request = identity()
        let initial = try await client.start(scope: scope, objective: "Dos tareas propias",
            children: [task("escribe"), task("resumen")], requestID: request, maxSteps: 6, timeout: 20)
        var goal = initial, approvals: [ApprovalOperation] = []
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while approvals.isEmpty && ContinuousClock.now < deadline {
            goal = try await client.get(goal); approvals = try await client.approvals(goal)
            if approvals.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        }
        let operation = try XCTUnwrap(approvals.first)
        XCTAssertEqual(operation.toolName, "workspace_write")
        XCTAssertNotEqual(operation.scope, scope)
        XCTAssertFalse(FileManager.default.fileExists(atPath: profile + "/workspace/coordinacion.txt"))
        XCTAssertFalse(goal.settled)
        let permitted = try await client.decide(operation, goal: goal, allow: true)
        XCTAssertTrue(permitted)
        let completed = try await final(client, goal)
        XCTAssertEqual(completed.status, "completed"); XCTAssertTrue(completed.parentSettled)
        XCTAssertEqual(Set(completed.children.map(\.sessionID)).count, 2)
        XCTAssertEqual(Set(completed.children.map(\.runID)).count, 2)
        XCTAssertTrue(completed.children.allSatisfy { $0.settled && $0.status == "completed" })
        let receipt = try XCTUnwrap(completed.children.flatMap(\.receipts).first)
        XCTAssertEqual(receipt.toolName, "workspace_write"); XCTAssertTrue(receipt.succeeded)
        XCTAssertTrue(receipt.verified); XCTAssertFalse(receipt.unknown)
        let bytes = try Data(contentsOf: URL(fileURLWithPath: profile + "/workspace/coordinacion.txt"))
        XCTAssertEqual(bytes, Data("JARVIS HIJO 7319".utf8))
        let trace = try await transport.call("fixture.coordination.trace")
        XCTAssertEqual(trace["calls"].array?.count, 2)
        XCTAssertTrue(trace["calls"].array?.allSatisfy {
            $0["history"].integer == 0 && $0["bound"].bool == false && $0["delegate_offered"].bool == false
        } == true)
        let emptyApprovals = try await client.approvals(completed)
        XCTAssertTrue(emptyApprovals.isEmpty)
        await client.close()
        let stopped = await firstHost.stop(); XCTAssertEqual(stopped, .stopped)

        let secondHost = try await startHost(profile)
        let reopenedTransport = LocalRPCClient(stateDirectory: profile)
        let reopened = DelegationClient(transport: reopenedTransport)
        let observedValue = try await reopened.lookup(scope: scope, requestID: request)
        let observed = try XCTUnwrap(observedValue)
        XCTAssertEqual(observed, completed)
        let listed = try await reopened.list(scope: scope)
        XCTAssertEqual(listed.map(\.id), [request])
        XCTAssertTrue(listed[0].settled)
        let noReplay = try await reopenedTransport.call("fixture.coordination.trace")
        XCTAssertEqual(noReplay["calls"].array?.count, 0)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: profile + "/workspace/coordinacion.txt")), bytes)
        await reopened.close()
        let secondStopped = await secondHost.stop(); XCTAssertEqual(secondStopped, .stopped)
    }

    @MainActor
    func testCancellationKeepsStoreBlockedUntilActualChildCleanupAndSQLSettlement() async throws {
        let profile = try directory(), host = try await startHost(profile)
        let transport = LocalRPCClient(stateDirectory: profile)
        let scope = try await parent(transport), store = DelegationStore()
        store.configure(transport); store.setScope(scope)
        await store.start(objective: "Custodia hasta cerrar", children: [task("limpieza"), task("espera")],
                          maxSteps: 6, timeout: 20)
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let trace = try await transport.call("fixture.coordination.trace")
            if trace["calls"].array?.count == 2 { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        await store.cancel()
        let blocked = try XCTUnwrap(store.goal)
        XCTAssertFalse(blocked.settled); XCTAssertTrue(store.blocksInteraction)
        XCTAssertEqual(blocked.status, "cancelling")
        let trace = try await transport.call("fixture.coordination.trace")
        XCTAssertEqual(trace["cleaning"].bool, true)
        XCTAssertGreaterThan(trace["active_runs"].integer ?? 0, 0)
        _ = try await transport.call("fixture.coordination.release")
        let client = DelegationClient(transport: transport)
        let finished = try await final(client, blocked)
        XCTAssertEqual(finished.status, "cancelled"); XCTAssertTrue(finished.settled)
        XCTAssertTrue(finished.children.allSatisfy(\.settled))
        await store.refresh(); XCTAssertFalse(store.blocksInteraction)
        await store.close()
        let stopped = await host.stop(); XCTAssertEqual(stopped, .stopped)
    }

    @MainActor
    func testAbruptOwnedHostExitRecoversInterruptedGoalAndNeverReplaysChildren() async throws {
        let profile = try directory(), firstHost = try await startHost(profile)
        let transport = LocalRPCClient(stateDirectory: profile), client = DelegationClient(transport: transport)
        let scope = try await parent(transport), request = identity()
        let initial = try await client.start(scope: scope, objective: "Interrupción propia",
            children: [task("espera")], requestID: request, maxSteps: 3, timeout: 20)
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let trace = try await transport.call("fixture.coordination.trace")
            if trace["calls"].array?.count == 1 { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertFalse(initial.settled)
        _ = try await transport.call("fixture.coordination.crash")
        try await Task.sleep(for: .milliseconds(100))
        await client.close()
        let crashed = await firstHost.stop(); XCTAssertEqual(crashed, .stopped)
        let secondHost = try await startHost(profile)
        let reopenedTransport = LocalRPCClient(stateDirectory: profile)
        let reopened = DelegationClient(transport: reopenedTransport)
        let recoveredValue = try await reopened.lookup(scope: scope, requestID: request)
        let recovered = try XCTUnwrap(recoveredValue)
        XCTAssertEqual(recovered.status, "interrupted"); XCTAssertTrue(recovered.settled)
        XCTAssertEqual(recovered.children.count, 1)
        XCTAssertEqual(recovered.children[0].status, "interrupted")
        XCTAssertTrue(recovered.children[0].unknownEffects)
        let repeated = try await reopened.start(scope: scope, objective: "Interrupción propia",
            children: [task("espera")], requestID: request, maxSteps: 3, timeout: 20)
        XCTAssertEqual(repeated, recovered)
        let trace = try await reopenedTransport.call("fixture.coordination.trace")
        XCTAssertEqual(trace["calls"].array?.count, 0)
        await reopened.close()
        let stopped = await secondHost.stop(); XCTAssertEqual(stopped, .stopped)
    }
}
