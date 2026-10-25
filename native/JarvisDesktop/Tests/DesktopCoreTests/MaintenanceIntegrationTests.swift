import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Real DesktopCore -> authenticated local RPC -> own JarvisService -> SQLite /
/// filesystem. Only the provider and owner decisions are synthetic fixtures.
final class MaintenanceIntegrationTests: XCTestCase {
    @MainActor
    private func withFixture(coldState: Bool = false, coldWal: Bool = false, _ operation: (MaintenanceClient, LocalRPCClient, String) async throws -> Void) async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"], let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa el entorno explícito de script/test_desktop.sh para mantenimiento con núcleo propio.")
        }
        let directory = "/private/tmp/jvs-maint-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let profile = directory + "/profile"
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, hashlib, json, os, signal, sqlite3, sys
        from pathlib import Path
        from jarvis_core.service import JarvisService
        from jarvis_core.state import StateStore
        from jarvis_core.memory import MemoryScope
        from jarvis_core.scheduler import ScheduleSpec
        class Peer:
          calls = 0
          async def account_status(self):
            self.calls += 1
            raise AssertionError('maintenance fixture must never query provider status')
          async def chat(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError('maintenance fixture must never invoke inference')
          async def aclose(self): pass
        async def main():
          parent=Path(sys.argv[1]); root=parent/'profile'; root.mkdir(mode=0o700)
          cold_state=sys.argv[2]=='cold'
          cold_wal=sys.argv[2]=='wal'
          wal_markers={}
          if cold_wal:
            from wal_fixture import make_wal
            wal_markers=make_wal(root)
          elif cold_state:
            with StateStore(root/'jarvis.sqlite3') as database:
              database.create_session('Conversación fría no vacía', agent_id='fixture')
          peer=Peer(); service=JarvisService(root, peer)
          service.agent_profiles.create('Agente sintético de mantenimiento', agent_id='fixture')
          if not cold_state and not cold_wal:
            service.store.create_session('Conversación sintética durable', agent_id='fixture')
            service.store.create_session('Conversación temporal excluida', temporary=True, agent_id='fixture')
          if not cold_wal:
            service.memory.upsert(MemoryScope('fixture', 'fixture_workspace'), 'marker',
              'MARCADOR SINTÉTICO MEMORIA SIN CUENTA', source_uri='jarvis://fixture-maintenance')
            service.schedule_store.create('Tarea sintética deshabilitada', 'Sin despacho', 'fixture',
              'fixture_workspace', ScheduleSpec('interval', interval_seconds=3600), enabled=False)
          # Explicit initial fixture files: a no-op document.update correctly
          # avoids persistence in production, so it cannot seed these files.
          for document in (service.skills.document, service._telegram_journal.document):
            file=document.directory/document.name
            data=json.dumps(document.read(),sort_keys=True,ensure_ascii=False,separators=(',',':'))
            descriptor=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(descriptor,'w',encoding='utf-8') as output:
              output.write(data)
          (service._workspace_path/'EXCLUDED_FIXTURE.txt').write_text('WORKSPACE NO INCLUIDO')
          names=tuple(name+suffix for name in ('jarvis.sqlite3','memory.sqlite3','schedules.sqlite3')
                      for suffix in ('','-wal','-shm','-journal'))+('profiles/agents.json',
                 'profiles/skills.json','telegram/telegram.json')
          def fingerprint():
            value=[]
            for name in names:
              file=root/name
              if not file.exists():
                value.append([name,None]); continue
              info=file.stat()
              value.append([name,info.st_dev,info.st_ino,info.st_mode&0o777,info.st_size,
                            info.st_mtime_ns,info.st_ctime_ns,hashlib.sha256(file.read_bytes()).hexdigest()])
            return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
          control={'allow_cleanup':True}
          if cold_wal:
            from jarvis_core.cold_wal import WalCaptureOwner
            native_cleanup=WalCaptureOwner.cleanup
            def cleanup(owner):
              if not control['allow_cleanup']:
                raise OSError('synthetic WAL cleanup held by native IPC fixture')
              return native_cleanup(owner)
            WalCaptureOwner.cleanup=cleanup
          async def trace(params):
            return {'provider_calls':peer.calls, 'sources_sha256':fingerprint(),
                    'runner_running':service.scheduler.running,'state_cold':service.store._disk is None,
                    'memory_cold':service.memory._disk is None,
                    'schedules_cold':service.schedule_store._connection is None}
          async def allow_cleanup(params):
            assert set(params)=={'allow'} and type(params['allow']) is bool
            control['allow_cleanup']=params['allow']; return {'ok':True}
          async def wal_result(params):
            assert not params and cold_wal
            result={}
            for role,table in (('state','sessions'),('memory','documents'),('schedules','schedules')):
              data=(parent/'wal-native-backup'/'data'/(role+'.sqlite3')).read_bytes()
              assert data[18:20]==b'\\1\\1'
              with sqlite3.connect(':memory:') as db:
                db.deserialize(data); db.execute('PRAGMA query_only=ON')
                rows=db.execute('SELECT title FROM '+table).fetchall()
                assert rows==[(wal_markers[role]['committed'],)]
              result[role]=rows[0][0]
            return result
          async def mutate(params):
            service.store.create_session('Cambio sintético posterior a revisión', agent_id='fixture')
            return {'ok':True}
          service.server._handlers.update({'fixture.maintenance.trace':trace,'fixture.maintenance.mutate':mutate,
            'fixture.maintenance.cleanup_allow':allow_cleanup,'fixture.maintenance.wal_result':wal_result})
          done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
          try:
            await service.start(); await done.wait()
          finally:
            control['allow_cleanup']=True
            await service.aclose()
        asyncio.run(main())
        """, directory, coldWal ? "wal" : coldState ? "cold" : "warm"]
        let testSource = URL(fileURLWithPath: source).deletingLastPathComponent().appendingPathComponent("tests").path
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source + ":" + testSource,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        let diagnosticPath = directory + "/fixture.stderr.log"
        let diagnosticFD = Darwin.open(diagnosticPath, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard diagnosticFD >= 0 else { throw RPCFailure.configuration }
        let diagnostic = FileHandle(fileDescriptor: diagnosticFD, closeOnDealloc: true)
        defer { try? diagnostic.close() }
        child.standardOutput = FileHandle.nullDevice; child.standardError = diagnostic
        try startFixtureProcess(child, directory: directory)
        let transport = LocalRPCClient(stateDirectory: profile)
        let client = MaintenanceClient(transport: transport)
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: profile) { child.isRunning }
            try await operation(client, transport, directory)
            await client.close()
        } catch {
            preserveFixtureDiagnostic(diagnosticPath)
            await client.close(); throw error
        }
    }

    @MainActor
    private func preserveFixtureDiagnostic(_ path: String) {
        // Only this child fixture's bounded stderr, never another process or
        // account. Keep diagnostics outside the normally removed fixture root.
        guard let input = try? FileHandle(forReadingFrom: URL(fileURLWithPath: path)) else { return }
        defer { try? input.close() }
        guard let data = try? input.read(upToCount: 16_384), !data.isEmpty else { return }
        let outputPath = "/private/tmp/jvs-maint-diagnostic-" + UUID().uuidString + ".log"
        let descriptor = Darwin.open(outputPath, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { return }
        let output = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? output.close() }
        try? output.write(contentsOf: data)
        print("Diagnóstico de fixture propio conservado: \(outputPath)")
    }

    @MainActor
    private func settled(_ client: MaintenanceClient, _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        var operation = initial
        for _ in 0..<250 {
            operation = try await client.operation(initial.id)
            if operation.settled { return operation }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTFail("El worker sintético no asentó su recibo dentro del presupuesto.")
        return operation
    }

    @MainActor
    private func syntheticOwner(_ client: MaintenanceClient, review: MaintenanceReview,
                                operation: MaintenanceOperation, allow: Bool) async throws {
        // Explicitly authorized synthetic test decision, never a real owner's
        // permission. No account, TCC, original installation or external effect.
        for _ in 0..<100 {
            let pending = try await client.pending()
            if let ticket = pending.approvals.first(where: { $0.matches(review, operation: operation) }) {
                let accepted = try await client.decide(ticket, review: review, operation: operation, allow: allow)
                XCTAssertTrue(accepted)
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTFail("No apareció el ticket central sintético exacto.")
    }

    @MainActor
    func testActualServiceDoctorBackupVerifyRestoreUseTypedCrossLanguageReceipts() async throws {
        try await withFixture { client, transport, directory in
            let before = try await transport.call("fixture.maintenance.trace")
            let diagnosticStart = try await client.doctor()
            let diagnostic = try await self.settled(client, diagnosticStart)
            XCTAssertTrue(diagnostic.success)
            let report = try DoctorSummary(diagnostic.result)
            XCTAssertEqual(report.status, "ok")
            XCTAssertTrue(report.checks.contains(where: { $0.role == "memory" && $0.integrity == "ok" }))
            let afterDoctor = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(afterDoctor["sources_sha256"], before["sources_sha256"])
            XCTAssertEqual(afterDoctor["provider_calls"].integer, 0)

            let review = try await client.reviewBackup(parent: directory, name: "backup-roundtrip")
            let operation = try await client.submit(review)
            try await self.syntheticOwner(client, review: review, operation: operation, allow: true)
            let created = try await self.settled(client, operation)
            XCTAssertEqual(created.phase, "complete"); XCTAssertTrue(created.success)
            XCTAssertTrue(created.receiptDurable); XCTAssertEqual(created.receipt?.effect, "published_verified_bytes")
            let backup = directory + "/backup-roundtrip"
            let manifest = try Data(contentsOf: URL(fileURLWithPath: backup + "/manifest.json"))
            let actualHash = SHA256.hash(data: manifest).map { String(format: "%02x", $0) }.joined()
            XCTAssertEqual(created.receipt?.manifestHash, actualHash)
            XCTAssertFalse(FileManager.default.fileExists(atPath: backup + "/workspace"))

            let checking = try await client.verify(path: backup, expectedHash: actualHash)
            let verifiedJob = try await self.settled(client, checking)
            let verified = try MaintenanceVerification(verifiedJob.result)
            XCTAssertTrue(verifiedJob.success); XCTAssertEqual(verified.manifestHash, actualHash)
            XCTAssertEqual(verified.artifactID, created.receipt?.artifactID)
            let restoreReview = try await client.reviewRestore(verificationID: verified.id, parent: directory, name: "restored-roundtrip")
            let restoreOperation = try await client.submit(restoreReview)
            try await self.syntheticOwner(client, review: restoreReview, operation: restoreOperation, allow: true)
            let restored = try await self.settled(client, restoreOperation)
            XCTAssertTrue(restored.success); XCTAssertEqual(restored.phase, "staged_disarmed")
            XCTAssertEqual(restored.receipt?.manifestHash, actualHash)
            XCTAssertEqual(restored.receipt?.effect, "new_disarmed_profile")
            XCTAssertTrue(FileManager.default.fileExists(atPath: directory + "/restored-roundtrip/restore_record.json"))
            let finalTrace = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(finalTrace["provider_calls"].integer, 0)
            XCTAssertEqual(finalTrace["runner_running"].bool, false)
        }
    }

    @MainActor
    func testColdWalServiceDoctorBackupVerifyRestoreKeepAllSourceFamiliesIntact() async throws {
        try await withFixture(coldWal: true) { client, transport, directory in
            let before = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(before["state_cold"], .bool(true))
            XCTAssertEqual(before["memory_cold"], .bool(true))
            XCTAssertEqual(before["schedules_cold"], .bool(true))
            let diagnostic = try await self.settled(client, client.doctor())
            XCTAssertTrue(diagnostic.success)
            let report = try DoctorSummary(diagnostic.result)
            for role in ["state", "memory", "schedules"] {
                XCTAssertTrue(report.checks.contains { $0.role == role && $0.integrity == "ok" && $0.source == "host_ram_snapshot" })
            }
            let review = try await client.reviewBackup(parent: directory, name: "wal-native-backup")
            XCTAssertEqual(Set(review.sqliteRecovery.map(\.role)), Set(["state", "memory", "schedules"]))
            let submitted = try await client.submit(review)
            try await self.syntheticOwner(client, review: review, operation: submitted, allow: true)
            let copied = try await self.settled(client, submitted)
            XCTAssertTrue(copied.success); XCTAssertTrue(copied.receiptDurable)
            XCTAssertFalse(copied.resourcesPending)
            let recovered = try await transport.call("fixture.maintenance.wal_result")
            for role in ["state", "memory", "schedules"] {
                XCTAssertEqual(recovered[role], .string("JARVIS WAL COMMIT TRES " + role))
            }
            let verifiedJob = try await self.settled(client, client.verify(path: directory + "/wal-native-backup", expectedHash: copied.receipt?.manifestHash))
            XCTAssertTrue(verifiedJob.success)
            let verification = try MaintenanceVerification(verifiedJob.result)
            let restore = try await client.reviewRestore(verificationID: verification.id, parent: directory, name: "wal-native-restored")
            XCTAssertTrue(restore.sqliteRecovery.isEmpty)
            let restoring = try await client.submit(restore)
            try await self.syntheticOwner(client, review: restore, operation: restoring, allow: true)
            let restored = try await self.settled(client, restoring)
            XCTAssertTrue(restored.success)
            XCTAssertEqual(restored.phase, "staged_disarmed")
            let final = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(final["sources_sha256"], before["sources_sha256"])
            XCTAssertEqual(final["provider_calls"], .number(0))
            XCTAssertEqual(final["state_cold"], .bool(true))
            XCTAssertEqual(final["memory_cold"], .bool(true))
            XCTAssertEqual(final["schedules_cold"], .bool(true))
        }
    }

    @MainActor
    func testNativeStoreKeepsActualWalCleanupOwnerUntilExplicitCleanupWithoutRecapture() async throws {
        try await withFixture(coldWal: true) { client, transport, _ in
            let before = try await transport.call("fixture.maintenance.trace")
            _ = try await transport.call("fixture.maintenance.cleanup_allow", params: ["allow": .bool(false)])
            let store = MaintenanceStore(maxPolls: 0)
            store.configure(client: client)
            let panel = UUID(); store.setVisible(panel, visible: true)
            await store.runDoctor()
            for _ in 0..<250 {
                await store.refresh()
                if store.operation?.cleanupOwnerPending == true && store.operation?.workerPending == false { break }
                try await Task.sleep(for: .milliseconds(20))
            }
            guard let pending = store.operation else { XCTFail("No se conservó la operación WAL."); return }
            XCTAssertTrue(pending.cleanupOwnerPending)
            XCTAssertEqual(pending.phase, "cleanup_pending")
            XCTAssertNil(pending.receipt); XCTAssertFalse(pending.receiptDurable)
            store.setVisible(panel, visible: false)
            XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.monitorPaused)
            XCTAssertFalse(store.mayPrepare); XCTAssertTrue(store.canCleanup)
            _ = try await transport.call("fixture.maintenance.cleanup_allow", params: ["allow": .bool(true)])
            await store.cleanup()
            for _ in 0..<250 {
                await store.refresh()
                if store.operation?.resourcesPending == false { break }
                try await Task.sleep(for: .milliseconds(20))
            }
            XCTAssertEqual(store.operation?.id, pending.id)
            XCTAssertEqual(store.operation?.phase, "failed")
            XCTAssertFalse(store.operation?.cleanupOwnerPending == true)
            XCTAssertFalse(store.operation?.success == true)
            XCTAssertNil(store.doctor)
            XCTAssertFalse(store.readAwaitingSettlement); XCTAssertFalse(store.blocksInteraction)
            let after = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(after["sources_sha256"], before["sources_sha256"])
            XCTAssertEqual(after["provider_calls"], .number(0))
            await store.close()
        }
    }

    @MainActor
    func testActualServiceDenialAndStaleReviewNeverPublishOrCallProvider() async throws {
        try await withFixture { client, transport, directory in
            let review = try await client.reviewBackup(parent: directory, name: "backup-denied")
            let operation = try await client.submit(review)
            try await self.syntheticOwner(client, review: review, operation: operation, allow: false)
            let denied = try await self.settled(client, operation)
            XCTAssertEqual(denied.phase, "denied"); XCTAssertFalse(denied.success)
            XCTAssertTrue(denied.receiptDurable)
            XCTAssertFalse(FileManager.default.fileExists(atPath: directory + "/backup-denied"))
            let stale = try await client.reviewBackup(parent: directory, name: "backup-stale")
            _ = try await transport.call("fixture.maintenance.mutate")
            do { _ = try await client.submit(stale); XCTFail("La revisión obsoleta no debe crear una operación nueva.") }
            catch { guard case .remote = error as? RPCFailure else { throw error } }
            XCTAssertFalse(FileManager.default.fileExists(atPath: directory + "/backup-stale"))
            let pending = try await client.pending(); XCTAssertTrue(pending.approvals.isEmpty)
            let status = try await client.status(); XCTAssertEqual(status.operations.count, 1)
            let trace = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(trace["provider_calls"].integer, 0)
        }
    }
    @MainActor
    func testColdNonemptyDeleteStateIsEligibleWithoutOpeningStoreThroughActualRPC() async throws {
        try await withFixture(coldState: true) { client, transport, directory in
            let before = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(before["state_cold"].bool, true)
            let diagnosticStart = try await client.doctor()
            let diagnostic = try await self.settled(client, diagnosticStart)
            XCTAssertTrue(diagnostic.success)
            let report = try DoctorSummary(diagnostic.result)
            XCTAssertEqual(report.status, "ok")
            let review = try await client.reviewBackup(parent: directory, name: "backup-cold-state")
            let state = try XCTUnwrap(review.sources.first(where: { $0.role == "state" }))
            XCTAssertEqual(state.disposition, "present")
            XCTAssertGreaterThan(state.bytes, 100)
            // The existing native client rejects unavailable sources; this is
            // an actual eligible cold image, not a fabricated owned connection.
            let operation = try await client.submit(review)
            try await self.syntheticOwner(client, review: review, operation: operation, allow: true)
            let created = try await self.settled(client, operation)
            XCTAssertTrue(created.success)
            XCTAssertEqual(created.receipt?.effect, "published_verified_bytes")
            let backup = directory + "/backup-cold-state"
            let manifest = try Data(contentsOf: URL(fileURLWithPath: backup + "/manifest.json"))
            let hash = SHA256.hash(data: manifest).map { String(format: "%02x", $0) }.joined()
            XCTAssertEqual(created.receipt?.manifestHash, hash)
            let verificationStart = try await client.verify(path: backup, expectedHash: hash)
            let verification = try await self.settled(client, verificationStart)
            XCTAssertTrue(verification.success)
            let after = try await transport.call("fixture.maintenance.trace")
            XCTAssertEqual(after["state_cold"].bool, true)
            XCTAssertEqual(after["sources_sha256"], before["sources_sha256"])
            XCTAssertEqual(after["provider_calls"].integer, 0)
        }
    }

}
