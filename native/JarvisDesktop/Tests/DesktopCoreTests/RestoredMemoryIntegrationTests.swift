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
      async def trace(params): return trace_snapshot()
      service.server._handlers['fixture.memory.trace']=trace
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: await service.aclose()
    asyncio.run(main())
    """
    @MainActor private func fixture(_ operation: (RestoredMemoryClient,LocalRPCClient) async throws -> Void) async throws {
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
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory:directory+"/profile",timeout:5) { child.isRunning }
            try await operation(client,transport); await client.close()
        } catch {
            if let data = try? Data(contentsOf:URL(fileURLWithPath:path)), !data.isEmpty {
                let destination = "/private/tmp/jvs-memory-diagnostic-" + UUID().uuidString + ".log"
                try? Data(data.prefix(16_384)).write(to:URL(fileURLWithPath:destination),options:.withoutOverwriting)
                try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:destination)
                print("Diagnóstico del fixture propio: \(destination)")
            }
            await client.close(); throw error
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
        try await fixture { client, transport in
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
        try await fixture { client, transport in
            let before = try await transport.call("fixture.memory.trace"), reviewed = try await self.review(client)
            let submitted = try await client.submit(reviewed); try await self.authorize(client,reviewed,submitted,allow:false)
            let denied = try await self.settle(client,submitted); XCTAssertEqual(denied.phase,"denied"); XCTAssertFalse(denied.success)
            let trace = try await transport.call("fixture.memory.trace")
            XCTAssertEqual(trace["file_sha256"],before["file_sha256"]); XCTAssertEqual(trace["schema"],.number(1))
            XCTAssertEqual(trace["destination_documents"],.number(0)); XCTAssertEqual(trace["copy_records"],.number(0)); XCTAssertEqual(trace["provider_calls"],.number(0))
        }
    }
}
