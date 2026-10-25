import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Real typed native -> local authenticated RPC -> own SQLite transaction.
/// The two owner decisions below are authorized synthetic fixture decisions.
final class RestoredMemoryIntegrationTests: XCTestCase {
    private static let script = """
    import asyncio, hashlib, os, signal, sqlite3, sys, threading, time
    from pathlib import Path
    from uuid import uuid4
    from jarvis_core.activation import activate_prepared_profile,review_prepared_profile
    from jarvis_core.host_profile import ProfileLease
    from jarvis_core.memory import MemoryScope,MemoryStore
    from jarvis_core.profiles import ProfilesStore
    from jarvis_core.recovery import DISARMS,EXCLUSIONS,PORTABLE_CONFIG,RecoveryContext,canonical
    from jarvis_core.service import JarvisService
    class NoSdkPeer:
      calls=0
      async def start(self):
        self.calls+=1; raise AssertionError('memory fixture attempted SDK start')
      async def account_status(self):
        self.calls+=1; raise AssertionError('memory fixture queried account')
      async def login(self):
        self.calls+=1; raise AssertionError('memory fixture attempted login')
      async def chat(self,*args,**kwargs):
        self.calls+=1; raise AssertionError('memory fixture attempted inference')
      async def aclose(self): pass
    def context():
      return RecoveryContext(uuid4().hex,'synthetic_fixture',0,lambda stage,write:None,threading.Event(),time.monotonic()+20)
    async def main():
      parent=Path(sys.argv[1]); root=parent/'profile'; root.mkdir(mode=0o700)
      origin=parent/'origin'; origin.mkdir(mode=0o700)
      old_scope=MemoryScope('jarvis','f'*64)
      with MemoryStore(origin/'memory.sqlite3') as source:
        source.upsert(old_scope,'first','MARCADOR SINTETICO SOLO SQLITE UNO',source_uri='jarvis://synthetic-memory/first')
        source.upsert(old_scope,'first','MARCADOR SINTETICO SOLO SQLITE UNO REVISION DOS',source_uri='jarvis://synthetic-memory/first')
        source.upsert(old_scope,'second','MARCADOR SINTETICO SOLO SQLITE DOS',source_uri='jarvis://synthetic-memory/second')
        source.upsert(MemoryScope('jarvis','f'*64,'session_fixture'),'excluded','NO APARECER EN INVENTARIO',source_uri='jarvis://synthetic-memory/session')
        image=source._disk.serialize()
      (root/'memory.sqlite3').write_bytes(image); (root/'memory.sqlite3').chmod(0o600)
      profiles=ProfilesStore(root/'profiles'); agent_file=root/'profiles'/'agents.json'
      agents_data=canonical(profiles.document.read())
      descriptor=os.open(agent_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
      with os.fdopen(descriptor,'wb') as output: output.write(agents_data)
      (root/'configuration.json').write_bytes(canonical(PORTABLE_CONFIG)); (root/'configuration.json').chmod(0o600)
      marker={'format':'jarvis-restore-record/1','profile_id':uuid4().hex,'source_artifact_id':uuid4().hex,
        'source_manifest_sha256':'e'*64,'source_profile_id':'synthetic_origin','source_generation':0,
        'activation_required':True,'replay_allowed':False,'disarms':list(DISARMS),'excluded':list(EXCLUSIONS)}
      (root/'restore_record.json').write_bytes(canonical(marker)); (root/'restore_record.json').chmod(0o600)
      # Independent setup admission only. The memory effect is NOT performed here.
      receipt=activate_prepared_profile(review_prepared_profile(root,context()),context())
      assert receipt.status=='completed'
      peer=NoSdkPeer(); lease=ProfileLease.acquire(root); service=JarvisService(root,peer,profile_lease=lease)
      def trace_snapshot():
        data=(root/'memory.sqlite3').read_bytes()
        with sqlite3.connect(':memory:') as database:
          database.deserialize(data)
          source=database.execute('SELECT document_id,text,revision FROM documents WHERE scope_key=? ORDER BY document_id',(old_scope.key,)).fetchall()
          dest=database.execute('SELECT document_id,text,revision FROM documents WHERE scope_key=? ORDER BY document_id',(MemoryScope('jarvis',service._workspace_id).key,)).fetchall()
          fields='key,title,source_uri,text,text_sha256,byte_length'
          source_payload=database.execute('SELECT '+fields+' FROM documents WHERE scope_key=? ORDER BY key',(old_scope.key,)).fetchall()
          destination_payload=database.execute('SELECT '+fields+' FROM documents WHERE scope_key=? ORDER BY key',(MemoryScope('jarvis',service._workspace_id).key,)).fetchall()
          schema=database.execute('PRAGMA user_version').fetchone()[0]
          copies=database.execute('SELECT count(*) FROM memory_scope_copies').fetchone()[0] if schema==2 else 0
          return {'schema':schema,'source_hash':hashlib.sha256(canonical(source)).hexdigest(),
            'source_payload_sha256':hashlib.sha256(canonical(source_payload)).hexdigest(),
            'destination_payload_sha256':hashlib.sha256(canonical(destination_payload)).hexdigest(),
            'destination_documents':len(dest),'ids_disjoint':not(set(x[0] for x in source)&set(x[0] for x in dest)),
            'destination_revision_one':all(x[2]==1 for x in dest),'copy_records':copies,
            'file_sha256':hashlib.sha256(data).hexdigest(),'provider_calls':peer.calls,'memory_was_opened':service.memory._disk is not None}
      # These handlers instrument ONLY this owned fixture. The real authority,
      # SQLite effect and authenticated main transport remain unchanged.
      gate_armed=False; status_entered=asyncio.Event(); status_release=asyncio.Event()
      decide_calls=0; accepted_decisions=0
      original_status=service.server._handlers['maintenance.status']
      original_decide=service.server._handlers['maintenance.decide']
      async def status(params):
        nonlocal gate_armed
        result=await original_status(params)
        if gate_armed and not params:
          gate_armed=False; status_entered.set(); await status_release.wait()
        return result
      async def decide(params):
        nonlocal decide_calls,accepted_decisions
        decide_calls+=1
        result=await original_decide(params)
        accepted_decisions+=int(result.get('accepted') is True)
        return result
      async def gate_next(params):
        nonlocal gate_armed
        status_entered.clear(); status_release.clear(); gate_armed=True
        return {'armed':True}
      async def release(params): status_release.set(); return {'released':True}
      async def trace(params):
        return dict(trace_snapshot(),decide_calls=decide_calls,
          accepted_decisions=accepted_decisions,status_gate_entered=status_entered.is_set(),
          status_gate_released=status_release.is_set())
      service.server._handlers.update({'fixture.memory.trace':trace,
        'maintenance.status':status,'maintenance.decide':decide,
        'fixture.memory.gate_next_status':gate_next,'fixture.memory.release_status':release})
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: await service.aclose()
    asyncio.run(main())
    """
    @MainActor private func fixture(_ operation: (RestoredMemoryClient,LocalRPCClient,LocalRPCClient,ProfileRuntimeGuard) async throws -> Void) async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else { throw XCTSkip("Usa las rutas explícitas del entorno de pruebas de escritorio.") }
        let directory = "/private/tmp/jvs-memory-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath:directory,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
        let child = Process(); child.executableURL = URL(fileURLWithPath:python); child.arguments = ["-c",Self.script,directory]
        child.environment = ["PATH":"/usr/bin:/bin","PYTHONPATH":source,"PYTHONNOUSERSITE":"1","PYTHONDONTWRITEBYTECODE":"1"]
        let path = directory + "/stderr.log", descriptor = Darwin.open(path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0o600)
        guard descriptor >= 0 else { throw RPCFailure.configuration }
        let log = FileHandle(fileDescriptor:descriptor,closeOnDealloc:true); child.standardError = log; child.standardOutput = FileHandle.nullDevice
        addTeardownBlock { try? log.close() }; try startFixtureProcess(child,directory:directory)
        let runtime = ProfileRuntimeGuard(), transport = LocalRPCClient(stateDirectory:directory+"/profile",runtime:runtime), client = RestoredMemoryClient(transport:transport,runtime:runtime)
        let control = LocalRPCClient(stateDirectory:directory+"/profile",runtime:runtime)
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory:directory+"/profile",timeout:5) { child.isRunning }
            try await operation(client,transport,control,runtime); await client.close(); await control.close()
        } catch {
            if let data = try? Data(contentsOf:URL(fileURLWithPath:path)), !data.isEmpty {
                let destination = "/private/tmp/jvs-memory-diagnostic-" + UUID().uuidString + ".log"
                try? Data(data.prefix(16_384)).write(to:URL(fileURLWithPath:destination),options:.withoutOverwriting)
                try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:destination)
                print("Diagnóstico del fixture propio: \(destination)")
            }
            await client.close(); await control.close(); throw error
        }
    }
    private func settle(_ client: RestoredMemoryClient, _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        let deadline = ContinuousClock.now.advanced(by:.seconds(10)); var row = initial
        while !row.settled, ContinuousClock.now < deadline { try await Task.sleep(for:.milliseconds(20)); row = try await client.operation(row.id) }
        guard row.settled else { throw RPCFailure.unavailable }; return row
    }
    private func authorize(_ client: RestoredMemoryClient, _ review: RestoredMemoryReview, _ operation: MaintenanceOperation, allow: Bool) async throws {
        let deadline = ContinuousClock.now.advanced(by:.seconds(5))
        while ContinuousClock.now < deadline {
            let pending = try await client.pending()
            if let ticket = pending.approvals.first(where: { review.matches($0,operation:operation) }) {
                let accepted = try await client.decide(ticket,review:review,operation:operation,allow:allow); XCTAssertTrue(accepted); return
            }
            try await Task.sleep(for:.milliseconds(20))
        }
        throw RPCFailure.unavailable
    }
    private func review(_ client: RestoredMemoryClient) async throws -> RestoredMemoryReview {
        let listed = try await settle(client,client.inventory(agentID:"jarvis"))
        XCTAssertTrue(listed.success,listed.errorCode ?? "Consulta sin éxito")
        let inventory = try RestoredMemoryInventory(listed.result["inventory"])
        XCTAssertEqual(inventory.schemaVersion,1); XCTAssertEqual(inventory.sourceKind,"cold_delete_snapshot")
        XCTAssertEqual(inventory.candidateCount,1); XCTAssertEqual(inventory.sources.count,1); XCTAssertEqual(inventory.excludedSessionScopes,1)
        XCTAssertTrue(inventory.destinationEmpty); XCTAssertFalse(inventory.destinationPresent)
        let source = try XCTUnwrap(inventory.sources.first)
        let prepared = try await settle(client,client.review(inventoryID:inventory.id,sourceHandle:source.id))
        let result = try RestoredMemoryReview(prepared.result["review"])
        XCTAssertTrue(result.matches(inventory,source:source)); XCTAssertTrue(result.migrationRequired)
        return result
    }
    @MainActor func testActualCopyUpgradesAtomicallyAndReturnsIndependentLocalObservationWithoutSDK() async throws {
        try await fixture { client, transport, _, _ in
            let before = try await transport.call("fixture.memory.trace")
            let reviewed = try await self.review(client)
            let afterReview = try await transport.call("fixture.memory.trace")
            XCTAssertEqual(before["file_sha256"],afterReview["file_sha256"]); XCTAssertEqual(afterReview["memory_was_opened"],.bool(false))
            let submitted = try await client.submit(reviewed)
            try await self.authorize(client,reviewed,submitted,allow:true)
            let completed = try await self.settle(client,submitted)
            XCTAssertTrue(completed.success); XCTAssertTrue(completed.receiptDurable); XCTAssertTrue(reviewed.matches(completed))
            XCTAssertEqual(completed.memoryCopyReceipt?.documents,2); XCTAssertEqual(completed.memoryCopyReceipt?.destinationGeneration,"1")
            let observed = try await self.settle(client,client.observation(operationID:completed.id,reviewDigest:reviewed.digest))
            let observation = try MemoryCopyObservation(observed.result)
            XCTAssertTrue(observation.documentsMatch); XCTAssertTrue(observation.generationMatches)
            XCTAssertEqual(observation.operationID,completed.id); XCTAssertEqual(observation.mappingHash,completed.memoryCopyReceipt?.mappingHash)
            let trace = try await transport.call("fixture.memory.trace")
            XCTAssertEqual(trace["provider_calls"],.number(0)); XCTAssertEqual(trace["schema"],.number(2))
            XCTAssertEqual(trace["source_hash"],before["source_hash"]); XCTAssertEqual(trace["destination_documents"],.number(2))
            XCTAssertEqual(trace["source_payload_sha256"],before["source_payload_sha256"])
            XCTAssertEqual(trace["destination_payload_sha256"],trace["source_payload_sha256"])
            XCTAssertEqual(trace["ids_disjoint"],.bool(true)); XCTAssertEqual(trace["destination_revision_one"],.bool(true)); XCTAssertEqual(trace["copy_records"],.number(1))
            let status = try await client.status(); XCTAssertGreaterThan(status.scope.generation,reviewed.scope.generation)
            XCTAssertEqual(completed.scope,reviewed.scope)
        }
    }
    @MainActor func testActualDenialLeavesV1BytesUntouchedAndDoesNotCreateCopyRecord() async throws {
        try await fixture { client, transport, _, _ in
            let before = try await transport.call("fixture.memory.trace"), reviewed = try await self.review(client)
            let submitted = try await client.submit(reviewed); try await self.authorize(client,reviewed,submitted,allow:false)
            let denied = try await self.settle(client,submitted); XCTAssertEqual(denied.phase,"denied"); XCTAssertFalse(denied.success)
            let trace = try await transport.call("fixture.memory.trace")
            XCTAssertEqual(trace["file_sha256"],before["file_sha256"]); XCTAssertEqual(trace["schema"],.number(1))
            XCTAssertEqual(trace["destination_documents"],.number(0)); XCTAssertEqual(trace["copy_records"],.number(0)); XCTAssertEqual(trace["provider_calls"],.number(0))
        }
    }
    @MainActor func testActualSerialTransportQueuesOneOwnerDecisionBehindReadAndDropsStaleStatus() async throws {
        try await fixture { _, transport, control, runtime in
            let pauses = StorePollingPause()
            let store = RestoredMemoryStore(runtime:runtime,pause:{ try await pauses.pause() })
            store.configure(transport); store.setVisible(UUID(),visible:true); store.selectAgent("jarvis")
            let before = try await control.call("fixture.memory.trace")
            await store.loadInventory(); store.selectSource(try XCTUnwrap(store.sources.first?.id)); await store.prepareReview(); await store.submit()
            let readyDeadline = ContinuousClock.now.advanced(by:.seconds(10))
            while store.approval == nil, ContinuousClock.now < readyDeadline {
                await store.refresh(); if store.approval == nil { try await Task.sleep(for:.milliseconds(20)) }
            }
            let ticket = try XCTUnwrap(store.approval), review = try XCTUnwrap(store.effectReview)
            XCTAssertTrue(store.canDecide)
            // Let any previous watch iteration finish, then hold only its next
            // pause. This makes the explicit refresh below own the gated read.
            await pauses.blockWatch()
            let pausedDeadline = ContinuousClock.now.advanced(by:.seconds(5))
            while !(await pauses.isWaiting()), ContinuousClock.now < pausedDeadline { await Task.yield() }
            let watchWaiting = await pauses.isWaiting(); XCTAssertTrue(watchWaiting)
            XCTAssertFalse(store.isRefreshing)
            _ = try await control.call("fixture.memory.gate_next_status")
            let oldRead = Task { await store.refresh() }
            do {
                let enteredDeadline = ContinuousClock.now.advanced(by:.seconds(5))
                var entered = false
                while ContinuousClock.now < enteredDeadline {
                    let value = try await control.call("fixture.memory.trace")
                    if value["status_gate_entered"] == .bool(true) { entered = true; break }
                    await Task.yield()
                }
                XCTAssertTrue(entered); XCTAssertTrue(store.isRefreshing); XCTAssertTrue(store.canDecide)
                let decision = Task { await store.decide(allow:true,ticket:ticket) }
                let queuedDeadline = ContinuousClock.now.advanced(by:.seconds(5))
                while !store.isRequesting, ContinuousClock.now < queuedDeadline { await Task.yield() }
                XCTAssertTrue(store.isRequesting)
                let held = try await control.call("fixture.memory.trace")
                XCTAssertEqual(held["decide_calls"],.number(0),"The serial main connection is still waiting for its status reply")
                XCTAssertEqual(held["accepted_decisions"],.number(0))
                XCTAssertEqual(held["status_gate_released"],.bool(false))
                XCTAssertEqual(held["schema"],.number(1)); XCTAssertEqual(held["file_sha256"],before["file_sha256"])
                _ = try await control.call("fixture.memory.release_status")
                await oldRead.value; await decision.value
                XCTAssertNil(store.approval,"The old read must not restore the clicked ticket")
                let settledDeadline = ContinuousClock.now.advanced(by:.seconds(10))
                while store.operation?.settled != true, ContinuousClock.now < settledDeadline {
                    await store.refresh(); if store.operation?.settled != true { try await Task.sleep(for:.milliseconds(20)) }
                }
                let completed = try XCTUnwrap(store.operation)
                XCTAssertTrue(completed.success,store.errorMessage ?? completed.errorCode ?? "No completed receipt")
                XCTAssertTrue(completed.receiptDurable); XCTAssertTrue(review.matches(completed))
                let final = try await control.call("fixture.memory.trace")
                XCTAssertEqual(final["decide_calls"],.number(1)); XCTAssertEqual(final["accepted_decisions"],.number(1))
                XCTAssertEqual(final["provider_calls"],.number(0)); XCTAssertEqual(final["copy_records"],.number(1))
                XCTAssertEqual(final["source_hash"],before["source_hash"])
                XCTAssertEqual(final["source_payload_sha256"],final["destination_payload_sha256"])
                await store.close()
            } catch {
                _ = try? await control.call("fixture.memory.release_status")
                await oldRead.value; await store.close(); throw error
            }
        }
    }

}


private actor StorePollingPause {
    private var blocked = false
    private var waiting = false
    func blockWatch() { blocked = true }
    func isWaiting() -> Bool { waiting }
    func pause() async throws {
        if blocked { waiting = true; try await Task.sleep(for:.seconds(60)) }
        else { try await Task.sleep(for:.milliseconds(20)) }
    }
}
