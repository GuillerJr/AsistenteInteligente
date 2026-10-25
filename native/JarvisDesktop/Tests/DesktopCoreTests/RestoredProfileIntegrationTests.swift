import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Actual native typed RPC -> own core/SQLite/central synthetic owner. No SDK,
/// credentials, account, installed app, staging or external action is involved.
final class RestoredProfileIntegrationTests: XCTestCase {
    private static let script = """
    import argparse, asyncio, os, signal, sqlite3, sys
    from pathlib import Path
    from uuid import uuid4
    from jarvis_core.service import JarvisService
    from jarvis_core import state
    from jarvis_core.profiles import ProfilesStore
    from jarvis_core.host_profile import ExpectedRestore, ProfileLease
    from jarvis_core.recovery import DISARMS, EXCLUSIONS, PORTABLE_CONFIG, canonical
    from jarvis_core.recovery_fs import FileIdentity
    class NoSdkPeer:
      calls=0
      async def start(self):
        self.calls+=1; raise AssertionError('local restore fixture attempted SDK start')
      async def account_status(self):
        self.calls+=1; raise AssertionError('local restore fixture queried SDK')
      async def login(self):
        self.calls+=1; raise AssertionError('local restore fixture attempted login')
      async def chat(self,*args,**kwargs):
        self.calls+=1; raise AssertionError('local restore fixture attempted inference')
      async def aclose(self): pass
    parser=argparse.ArgumentParser()
    parser.add_argument('parent'); parser.add_argument('mode')
    for flag in ('device','inode','marker-sha256','admission-sha256','operation-id'):
      parser.add_argument('--expected-restore-'+flag)
    args=parser.parse_args()
    async def main():
      parent=Path(args.parent); prepared=parent/'restored'; root=parent/'host'
      if args.mode=='old':
        prepared.mkdir(mode=0o700)
        # A portable SQLite image has no runtime StateStore .lock. Build it
        # from our own exact schema, as the core snapshot fixtures do.
        with sqlite3.connect(prepared/'jarvis.sqlite3') as database:
          database.executescript(state._SCHEMA)
          database.execute('PRAGMA application_id='+str(state._APPLICATION_ID))
          database.execute('PRAGMA user_version='+str(state._SCHEMA_VERSION))
          database.execute("INSERT INTO sessions VALUES(?,?,0,1,NULL,'jarvis',NULL)",(uuid4().hex,'CONVERSACION SINTETICA RESTAURADA'))
        (prepared/'jarvis.sqlite3').chmod(0o600)
        profiles=ProfilesStore(prepared/'profiles')
        data=canonical(profiles.document.read())
        agent_file=prepared/'profiles'/'agents.json'
        fd=os.open(agent_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output: output.write(data)
        (prepared/'configuration.json').write_bytes(canonical(PORTABLE_CONFIG))
        (prepared/'configuration.json').chmod(0o600)
        record={'format':'jarvis-restore-record/1','profile_id':uuid4().hex,
          'source_artifact_id':uuid4().hex,'source_manifest_sha256':'f'*64,
          'source_profile_id':'synthetic_origin','source_generation':0,
          'activation_required':True,'replay_allowed':False,
          'disarms':list(DISARMS),'excluded':list(EXCLUSIONS)}
        (prepared/'restore_record.json').write_bytes(canonical(record))
        (prepared/'restore_record.json').chmod(0o600)
        root.mkdir(mode=0o700); lease=None
      else:
        root=prepared
        expected=ExpectedRestore(FileIdentity(int(args.expected_restore_device),int(args.expected_restore_inode)),
          args.expected_restore_marker_sha256,args.expected_restore_admission_sha256,args.expected_restore_operation_id)
        lease=ProfileLease.acquire(root,expected_restore=expected)
      peer=NoSdkPeer(); service=JarvisService(root,peer,profile_lease=lease)
      delayed=asyncio.Event(); release=asyncio.Event()
      async def trace(params):
        return {'provider_calls':peer.calls,'delayed_started':delayed.is_set(),
          'sessions':[row.ref.session_id for row in service.store.list_sessions()],
          'prepared_sessions':_prepared_sessions() if args.mode=='old' else []}
      def _prepared_sessions():
        with sqlite3.connect(':memory:') as database:
          database.deserialize((prepared/'jarvis.sqlite3').read_bytes())
          return [row[0] for row in database.execute('SELECT session_id FROM sessions ORDER BY created_ns,session_id')]
      async def later(params):
        delayed.set(); await release.wait(); return {'marker':'OLD_CALLBACK_MUST_NOT_ARRIVE'}
      async def unblock(params): release.set(); return {'ok':True}
      service.server._handlers.update({'fixture.restore.trace':trace,'fixture.restore.delayed':later,'fixture.restore.release':unblock})
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: await service.aclose()
    asyncio.run(main())
    """

    @MainActor
    private func fixture(parent: String, mode: String, options: LaunchOptions? = nil) throws -> (Process, ServiceProcess) {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else { throw XCTSkip("Usa el entorno explícito de script/test_desktop.sh") }
        let child = Process(); child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", Self.script, parent, mode] + (options?.expectedRestore?.arguments ?? [])
        child.environment = ["PATH":"/usr/bin:/bin", "PYTHONPATH":source, "PYTHONNOUSERSITE":"1", "PYTHONDONTWRITEBYTECODE":"1"]
        child.standardOutput = FileHandle.nullDevice
        let logs = mode == "old" ? parent : "/private/tmp/jvs-restore-child-" + UUID().uuidString.prefix(8)
        if mode != "old" { try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: false, attributes: [.posixPermissions:0o700]) }
        let log = logs + "/stderr.log", fd = Darwin.open(log, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw RPCFailure.configuration }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true); child.standardError = handle
        addTeardownBlock { try? handle.close() }
        try startFixtureProcess(child, directory: logs)
        let service = ServiceProcess(makeChild: { _ in FoundationServiceChild(child: child, emit: { Darwin.kill(child.processIdentifier,$0) == 0 }) }, now: { ProcessInfo.processInfo.systemUptime }, sleep: { try await Task.sleep(for:.seconds($0)) }, gracePeriod: 5)
        let launch = try options ?? LaunchOptions.parse(["--python",python,"--source",source,"--state",parent+"/host","--codex","/usr/bin/false","--provider-state",parent+"/old-account-not-inherited"])
        try service.start(launch)
        addTeardownBlock { @MainActor in
            let closed = await service.stop()
            if !closed || service.lastStopReport?.graceful != true {
                XCTFail("No se confirmó el cierre limpio del hijo sintético propio; diagnóstico en \(log)")
            }
        }
        return (child, service)
    }
    @MainActor
    private func settle(_ client: RestoreProfileClient, _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        let deadline = ContinuousClock.now.advanced(by: .seconds(10))
        while ContinuousClock.now < deadline {
            let row = try await client.operation(initial.id)
            if row.settled { return row }
            try await Task.sleep(for:.milliseconds(20))
        }
        throw RPCFailure.unavailable
    }
    @MainActor
    private func start() async throws -> (String, ServiceProcess, LocalRPCClient, RestoreProfileClient) {
        let parent = "/private/tmp/jvs-restore-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath:parent,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
        let (_, service) = try fixture(parent:parent,mode:"old")
        try await ServiceBootstrap.waitForHealth(stateDirectory:parent+"/host",timeout:5) { service.isRunning }
        let transport = LocalRPCClient(stateDirectory:parent+"/host")
        let client = RestoreProfileClient(transport:transport)
        return (parent,service,transport,client)
    }
    @MainActor
    private func authorize(_ client: RestoreProfileClient, review: RestoreProfileReview, initial: MaintenanceOperation, allow: Bool) async throws {
        let deadline = ContinuousClock.now.advanced(by:.seconds(5))
        while ContinuousClock.now < deadline {
            let pending = try await client.pending()
            if let approval = pending.approvals.first(where: { review.matches($0,operation:initial) }) {
                let accepted = try await client.decide(approval,review:review,operation:initial,allow:allow)
                XCTAssertTrue(accepted); return
            }
            try await Task.sleep(for:.milliseconds(20))
        }
        throw RPCFailure.unavailable
    }
    @MainActor
    func testActualActivationHandoffAndInitialLocalStatusNeverQueryAccountOrMoveIdentity() async throws {
        let (parent,old,transport,client) = try await start()
        let source = try await client.localStatus()
        let before = try await transport.call("fixture.restore.trace")
        let reviewed = try await settle(client,client.review(path:parent+"/restored"))
        XCTAssertEqual(reviewed.phase,"complete",reviewed.errorCode ?? "Sin diagnóstico estable")
        let review = try RestoreProfileReview(reviewed.result["review"])
        let submitted = try await client.activate(review)
        try await authorize(client,review:review,initial:submitted,allow:true) // Synthetic owner only.
        let activated = try await settle(client,submitted)
        let target = try ProfileOpenTarget(review:review,operation:activated)
        XCTAssertEqual(activated.phase,"activated_not_opened"); XCTAssertTrue(activated.receiptDurable)
        let inspected = try await settle(client,client.inspect(path:target.path,marker:target.markerHash,admission:target.admissionHash))
        XCTAssertTrue(try RestoreProfileInspection(inspected.result["admission"]).openAvailable)
        let gate = ProfileHandoffGate(), predecessor = try XCTUnwrap(old.ownedIdentity)
        let env = ProcessInfo.processInfo.environment
        let sourceOptions = try LaunchOptions.parse(["--python",env["JARVIS_DESKTOP_PYTHON"]!,"--source",env["JARVIS_DESKTOP_SOURCE"]!,"--state",parent+"/host","--codex","/usr/bin/false","--provider-state",parent+"/old-account-not-inherited"])
        var newService: ServiceProcess?, starts = 0
        try await gate.open(target:target,source:sourceOptions,origin:source.identity,predecessor:predecessor,retireAndStop: {
            await client.close(); _ = await old.stop(); return old.lastStopReport
        },start: { options in
            starts += 1; XCTAssertNil(options.providerState)
            let (_, next) = try self.fixture(parent:parent,mode:"new",options:options); newService = next
            try await ServiceBootstrap.waitForHealth(stateDirectory:target.path,timeout:5) { next.isRunning }
        })
        XCTAssertEqual(starts,1); XCTAssertTrue(old.lastStopReport?.graceful == true)
        let nextTransport = LocalRPCClient(stateDirectory:target.path), next = RestoreProfileClient(transport:nextTransport)
        let local = try await next.localStatus()
        XCTAssertTrue(local.matches(target)); XCTAssertNotEqual(local.scope.profileID,source.scope.profileID)
        XCTAssertFalse(local.providerQueryAllowed); XCTAssertFalse(local.providerSelected)
        let localCapabilities = try await nextTransport.call("service.status")
        XCTAssertEqual(localCapabilities["provider"]["connected"],.bool(false))
        XCTAssertEqual(localCapabilities["capabilities"]["memory"],.bool(true))
        let trace = try await nextTransport.call("fixture.restore.trace")
        XCTAssertEqual(trace["provider_calls"].integer,0)
        XCTAssertEqual(trace["sessions"],before["prepared_sessions"])
        await next.close(); let closed = await newService!.stop(); XCTAssertTrue(closed)
    }
    @MainActor
    func testDeniedActivationHasNoAdmissionAndRetiredInflightReadCannotDeliverOldData() async throws {
        let (parent,_,transport,client) = try await start()
        let reviewed = try await settle(client,client.review(path:parent+"/restored"))
        XCTAssertEqual(reviewed.phase,"complete",reviewed.errorCode ?? "Sin diagnóstico estable")
        let review = try RestoreProfileReview(reviewed.result["review"]), submitted = try await client.activate(review)
        try await authorize(client,review:review,initial:submitted,allow:false)
        let denied = try await settle(client,submitted)
        XCTAssertEqual(denied.phase,"denied")
        XCTAssertThrowsError(try ProfileOpenTarget(review:review,operation:denied))
        XCTAssertFalse(FileManager.default.fileExists(atPath:parent+"/restored/restore_admission.json"))
        let runtime = ProfileRuntimeGuard(), delayed = LocalRPCClient(stateDirectory:parent+"/host",runtime:runtime)
        let result = Task { try await delayed.call("fixture.restore.delayed") }
        let deadline = ContinuousClock.now.advanced(by:.seconds(5))
        while true {
            let trace = try await transport.call("fixture.restore.trace")
            if trace["delayed_started"] == .bool(true) { XCTAssertEqual(trace["provider_calls"].integer,0); break }
            guard ContinuousClock.now < deadline else { result.cancel(); throw RPCFailure.unavailable }
            await Task.yield()
        }
        runtime.retire(); _ = try await transport.call("fixture.restore.release")
        do { _ = try await result.value; XCTFail("Old read delivered into retired runtime") } catch { XCTAssertTrue(error is CancellationError) }
        await delayed.close(); await client.close()
    }
}
