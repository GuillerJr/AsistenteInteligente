import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Real Swift -> signed Unix RPC -> own Python/SQLite, with an authored model
/// peer. No account, owner profile, installed application or external effect.
final class SessionCoreIntegrationTests: XCTestCase {
    private static let script = """
    import asyncio, hashlib, json, signal, sys
    from pathlib import Path
    from uuid import uuid4
    from jarvis_core.service import JarvisService
    from jarvis_core.state import SessionRef
    class Peer:
      def __init__(self): self.calls=[]
      async def account_status(self): return {'connected':True,'mode':'chatgpt'}
      async def chat(self,message,*,on_thread,on_event,**options):
        history=json.dumps(options.get('history',[]),ensure_ascii=False)
        self.calls.append({'marker':'TOPACIO-4826' in history,
          'decision':'Conservar contrato-sintetico.txt' in history,
          'receipts':'historical_receipts' in history,
          'unknown':'unknown' in history,'bound':options.get('thread_id') is not None})
        on_thread(options.get('thread_id') or 'synthetic_'+uuid4().hex)
        return {'answer':'Respuesta del peer propio; no acredita inferencia autenticada.'}
      async def aclose(self): pass
    async def main():
      root=Path(sys.argv[1]); peer=Peer(); service=JarvisService(root,peer)
      ids_path=root/'fixture-session-ids.json'
      if not ids_path.exists():
        source=service.store.create_session('Conversacion sintetica de contexto').ref
        for index in range(8):
          prompt='Conserva TOPACIO-4826 y los nombres exactos.' if index==0 else 'Pregunta sintetica '+str(index)
          run=service.store.create_run(source,user_text=prompt)
          service.store.finish_run(source,run.run_id,status='completed',outcome='known',
            receipt={'action_receipts':[]},assistant_text='Respuesta sintetica '+str(index))
        uncertain=service.store.create_run(source,user_text='No repetir esta accion incierta')
        service.store.finish_run(source,uncertain.run_id,status='cancelled',outcome='unknown',
          receipt={'action_receipts':[{'tool_name':'workspace_write','succeeded':False,
            'verified':False,'effect_status':'unknown','operation_id':uuid4().hex}]})
        originals=[vars(row) for row in service.store.list_messages(source)]
        ids_path.write_text(json.dumps({'source':source.session_id,'uncertain':uncertain.run_id,
          'originals':originals},sort_keys=True)); ids_path.chmod(0o600)
      ids=json.loads(ids_path.read_text()); dropped={'id':None,'writes':0,'armed':False}
      released=asyncio.Event()
      original=service.server._handlers['sessions.compact']
      async def withhold(params):
        result=await original(params)
        if dropped['armed']:
          dropped.update(id=params['operation_id'],writes=dropped['writes']+1,armed=False)
          await released.wait()
        return result
      async def arm(params):
        dropped['armed']=True; released.clear(); return {'ok':True}
      async def release(params): released.set(); return {'ok':True}
      async def trace(params):
        source=SessionRef(ids['source'])
        current=[vars(row) for row in service.store.list_messages(source)]
        operations=[row for row in current if row['role']=='system' and row['text'].startswith('JARVIS_SESSION_OPERATION_V1\\n')]
        return {'source':ids['source'],'calls':peer.calls,'dropped_id':dropped['id'],
          'withheld_writes':dropped['writes'],'operations':len(operations),
          'originals_intact':current[:len(ids['originals'])]==ids['originals'],
          'source_runs':len(service.store.list_runs(source))}
      service.server._handlers.update({'sessions.compact':withhold,'fixture.context.arm':arm,
        'fixture.context.release':release,'fixture.context.trace':trace})
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: released.set(); await service.aclose()
    asyncio.run(main())
    """

    @MainActor
    private func startHost(_ profile: String) async throws -> FixtureProcess {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para integrar Swift con Python.")
        }
        let logs = "/private/tmp/jarvis-session-core-child-" + UUID().uuidString.prefix(8)
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

    @MainActor
    private func run(_ transport: LocalRPCClient, scope: SessionScope, message: String) async throws {
        var params = scope.params; params["message"] = .string(message)
        let started = try await transport.call("runs.start", params: params)
        let identity = try XCTUnwrap(started["run_id"].string)
        var query = scope.params; query["run_id"] = .string(identity)
        let deadline = ContinuousClock.now.advanced(by: .seconds(8))
        while ContinuousClock.now < deadline {
            let row = try await transport.call("runs.get", params: query)
            if let status = row["run"]["status"].string,
               ["completed", "failed", "cancelled", "interrupted"].contains(status) {
                XCTAssertEqual(status, "completed"); return
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor
    func testActualCompactionRewindLostACKAndTwoRestartsKeepIdentityWithoutReplay() async throws {
        let parent = "/private/tmp/jarvis-session-core-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: parent, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: parent) }
        let profile = parent + "/profile", firstHost = try await startHost(profile)
        let firstTransport = LocalRPCClient(stateDirectory: profile)
        let first = SessionCoreClient(transport: firstTransport)
        let initialTrace = try await firstTransport.call("fixture.context.trace")
        let source = SessionScope(sessionID: try XCTUnwrap(initialTrace["source"].string), temporary: false)
        let before = try await first.context(source)
        XCTAssertEqual(before.revision, 17); XCTAssertNil(before.checkpoint)
        let compactID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let compacted = try await first.compact(source, revision: before.revision, operationID: compactID,
            keepRecent: 2, decisions: ["Conservar contrato-sintetico.txt"])
        XCTAssertEqual(compacted.context?.checkpoint?.receipts, 9)
        XCTAssertEqual(compacted.context?.checkpoint?.decisions, 1)
        XCTAssertGreaterThan(compacted.context?.checkpoint?.omittedMessages ?? 0, 0)
        let rewindID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let rewound = try await first.rewind(source, revision: try XCTUnwrap(compacted.context?.revision),
            until: 2, operationID: rewindID, title: "Rama sintetica de la fase A")
        let branch = try XCTUnwrap(rewound.branch)
        XCTAssertEqual(branch.parentID, source.sessionID); XCTAssertFalse(branch.scope.temporary)
        let branchContext = try await first.context(branch.scope)
        XCTAssertEqual(branchContext.checkpoint?.receipts, 9)
        try await run(firstTransport, scope: branch.scope, message: "Continuar con una respuesta sintetica")
        let afterTurn = try await firstTransport.call("fixture.context.trace")
        let call = try XCTUnwrap(afterTurn["calls"].array?.last)
        XCTAssertEqual(call["marker"].bool, true); XCTAssertEqual(call["receipts"].bool, true)
        XCTAssertEqual(call["unknown"].bool, true); XCTAssertEqual(call["bound"].bool, false)
        XCTAssertEqual(afterTurn["originals_intact"].bool, true)
        XCTAssertEqual(afterTurn["source_runs"].integer, 9)
        let temporaryValue = try await firstTransport.call("sessions.create", params: [
            "title": .string("Temporal sintetica"), "temporary": .bool(true)])
        let temporary = SessionScope(sessionID: try XCTUnwrap(temporaryValue["session"]["session_id"].string), temporary: true)
        try await run(firstTransport, scope: temporary, message: "MARCADOR TEMPORAL NO PERSISTE")
        let tempBefore = try await first.context(temporary)
        let tempID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        _ = try await first.compact(temporary, revision: tempBefore.revision, operationID: tempID)
        let sourceBeforeRestart = try await first.context(source)
        await first.close()
        let firstStop = await firstHost.stop(); XCTAssertEqual(firstStop, .stopped)

        let secondHost = try await startHost(profile)
        let secondTransport = LocalRPCClient(stateDirectory: profile)
        let second = SessionCoreClient(transport: secondTransport)
        let restarted = try await second.context(source)
        XCTAssertEqual(restarted, sourceBeforeRestart)
        let branchObservation = try await second.operation(source, id: rewindID, kind: "rewind")
        let branchReceipt = try XCTUnwrap(branchObservation)
        XCTAssertEqual(branchReceipt.branch?.scope, branch.scope); XCTAssertTrue(branchReceipt.isHistoricalObservation)
        let compactObservation = try await second.operation(source, id: compactID, kind: "compact")
        let compactReceipt = try XCTUnwrap(compactObservation)
        XCTAssertEqual(compactReceipt.context?.checkpoint?.id, compacted.context?.checkpoint?.id)
        let firstPage = try await second.operations(source, limit: 1)
        XCTAssertEqual(firstPage.operations.map(\.id), [rewindID]); XCTAssertTrue(firstPage.hasMore)
        XCTAssertEqual(firstPage.nextOffset, 1)
        let secondPage = try await second.operations(source, offset: firstPage.nextOffset, limit: 1)
        XCTAssertEqual(secondPage.operations.map(\.id), [compactID]); XCTAssertFalse(secondPage.hasMore)
        XCTAssertTrue(secondPage.operations.allSatisfy(\.isHistoricalObservation))
        let branchPage = try await second.operations(branch.scope)
        XCTAssertTrue(branchPage.operations.isEmpty)
        do { _ = try await second.context(temporary); XCTFail("La sesión temporal no debe sobrevivir.") }
        catch RPCFailure.remote(let code, _) { XCTAssertEqual(code, -32010) }
        let beforeLost = try await secondTransport.call("fixture.context.trace")
        XCTAssertEqual(beforeLost["calls"].array?.count, 0)
        XCTAssertEqual(beforeLost["operations"].integer, 2)
        let lossyTransport = LocalRPCClient(stateDirectory: profile, timeout: 0.25)
        let store = SessionCoreStore(); store.configure(lossyTransport)
        store.setScope(source); await store.refresh()
        _ = try await secondTransport.call("fixture.context.arm")
        await store.compact(decisions: ["Decisión después del reinicio"])
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertTrue(store.blocksInteraction)
        let lostID = try XCTUnwrap(store.pendingOperationID)
        let withheld = try await secondTransport.call("fixture.context.trace")
        XCTAssertEqual(withheld["dropped_id"].string, lostID)
        XCTAssertEqual(withheld["withheld_writes"].integer, 1)
        await store.refresh()
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertEqual(store.result?.id, lostID)
        XCTAssertEqual(store.result?.isHistoricalObservation, true)
        XCTAssertNil(store.errorMessage)
        _ = try await secondTransport.call("fixture.context.release")
        let settledTrace = try await secondTransport.call("fixture.context.trace")
        XCTAssertEqual(settledTrace["operations"].integer, 3)
        XCTAssertEqual(settledTrace["withheld_writes"].integer, 1)
        XCTAssertEqual(settledTrace["originals_intact"].bool, true)
        let beforeFork = try await second.context(source)
        XCTAssertEqual(beforeFork.checkpoint?.decisions, 2)
        let forkID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let fullForkOperation = try await second.fork(source, revision: beforeFork.revision,
            operationID: forkID, title: "Rama completa con decisiones conservadas")
        let fullFork = try XCTUnwrap(fullForkOperation.branch)
        XCTAssertEqual(fullFork.sourceRevision, beforeFork.revision)
        XCTAssertEqual(fullFork.throughSequence, beforeFork.revision)
        let fullForkContext = try await second.context(fullFork.scope)
        XCTAssertEqual(fullForkContext.checkpoint?.decisions, 2)
        XCTAssertEqual(fullForkContext.checkpoint?.receipts, 9)
        let emptyParentValue = try await secondTransport.call("sessions.create", params: [
            "title": .string("Origen vacío para ramificar"), "temporary": .bool(false)])
        let emptyParent = SessionScope(sessionID: try XCTUnwrap(emptyParentValue["session"]["session_id"].string), temporary: false)
        let emptyForkID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let emptyForkOperation = try await second.fork(emptyParent, revision: 0,
            operationID: emptyForkID, title: "Rama vacía")
        let emptyFork = try XCTUnwrap(emptyForkOperation.branch)
        XCTAssertEqual(emptyFork.sourceRevision, 0); XCTAssertEqual(emptyFork.throughSequence, 0)
        let emptyContext = try await second.context(emptyFork.scope)
        XCTAssertEqual(emptyContext.revision, 0); XCTAssertNil(emptyContext.checkpoint)
        let latest = try await second.context(source)
        await store.close(); await second.close()
        let secondStop = await secondHost.stop(); XCTAssertEqual(secondStop, .stopped)

        let thirdHost = try await startHost(profile)
        let thirdTransport = LocalRPCClient(stateDirectory: profile)
        let third = SessionCoreClient(transport: thirdTransport)
        let thirdContext = try await third.context(source)
        XCTAssertEqual(thirdContext, latest)
        let lossObservation = try await third.operation(source, id: lostID, kind: "compact")
        let durableLoss = try XCTUnwrap(lossObservation)
        XCTAssertTrue(durableLoss.isHistoricalObservation)
        XCTAssertEqual(durableLoss.context?.checkpoint?.id, latest.checkpoint?.id)
        let finalTrace = try await thirdTransport.call("fixture.context.trace")
        XCTAssertEqual(finalTrace["operations"].integer, 4)
        XCTAssertEqual(finalTrace["calls"].array?.count, 0)
        XCTAssertEqual(finalTrace["originals_intact"].bool, true)
        let recoveredCatalog = try await third.operations(source)
        XCTAssertEqual(recoveredCatalog.operations.map(\.id), [forkID, lostID, rewindID, compactID])
        XCTAssertFalse(recoveredCatalog.hasMore)
        XCTAssertTrue(recoveredCatalog.operations.allSatisfy(\.isHistoricalObservation))
        let forkObservation = try await third.operation(source, id: forkID, kind: "fork")
        XCTAssertEqual(forkObservation?.branch?.scope, fullFork.scope)
        XCTAssertEqual(forkObservation?.isHistoricalObservation, true)
        let emptyObservation = try await third.operation(emptyParent, id: emptyForkID, kind: "fork")
        XCTAssertEqual(emptyObservation?.branch?.scope, emptyFork.scope)
        let stillEmpty = try await third.context(emptyFork.scope)
        XCTAssertEqual(stillEmpty.revision, 0); XCTAssertNil(stillEmpty.checkpoint)
        await third.close()
        let thirdStop = await thirdHost.stop(); XCTAssertEqual(thirdStop, .stopped)
    }

    @MainActor
    func testHistoricalCompactionReceiptRejectsAnotherSessionScope() async throws {
        let source = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        let operation = String(repeating: "e", count: 32)
        let client = SessionCoreClient(request: { _, _ in .object(["operation": .object([
            "operation_id": .string(operation), "operation_kind": .string("compact"),
            "recorded": .bool(true), "historical_ack_established": .bool(false), "permits_replay": .bool(false),
            "result": .object(["session": .object([
                "session_id": .string(String(repeating: "b", count: 32)), "temporary": .bool(false)]),
                "context": .object(["revision": .number(4), "message_count": .number(4),
                    "checkpoint": .object(["checkpoint_id": .string(String(repeating: "c", count: 32)),
                        "source_revision": .number(2), "source_digest": .string(String(repeating: "d", count: 64)),
                        "through_sequence": .number(2), "kept_message_count": .number(2), "omitted_message_count": .number(0),
                        "byte_length": .number(384), "decisions_count": .number(0), "receipts_count": .number(0)])])])])]) })
        do { _ = try await client.operation(source, id: operation, kind: "compact"); XCTFail("El recibo es de otra sesión.") }
        catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
    }
}
