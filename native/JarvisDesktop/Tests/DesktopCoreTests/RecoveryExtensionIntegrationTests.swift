import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Actual native DTO/client -> authenticated IPC -> own service and private
/// files. All accounts, user decisions and release payloads are synthetic.
final class RecoveryExtensionIntegrationTests: XCTestCase {
    @MainActor private func fixture(_ mode: String = "workspace", _ body: (RecoveryExtensionClient, LocalRPCClient, String) async throws -> Void) async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"], let source = environment["JARVIS_DESKTOP_SOURCE"] else { throw XCTSkip("Usa script/test_desktop.sh para el IPC propio de recuperación.") }
        let directory = "/private/tmp/jarvis-recovery-native-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let child = Process(); child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, hashlib, json, os, signal, sys
        from pathlib import Path
        from jarvis_core.service import JarvisService
        class Peer:
          calls=0
          async def account_status(self):
            self.calls+=1; raise AssertionError('no provider status in fixture')
          async def chat(self,*a,**k):
            self.calls+=1; raise AssertionError('no inference in fixture')
          async def aclose(self): pass
        async def main():
          parent=Path(sys.argv[1]); mode=sys.argv[2]; root=parent/'profile'
          if mode=='fts':
            from test_cold_repair import memory_fixture
            memory_fixture(root,'missing')
          elif mode=='wal':
            from wal_fixture import make_wal
            make_wal(root,('memory',))
            file=root/'memory.sqlite3-wal'; raw=file.read_bytes()
            file.write_bytes(raw+raw[-(24+int.from_bytes(raw[8:12],'big')):][:101])
          else: root.mkdir(mode=0o700)
          peer=Peer(); service=JarvisService(root,peer)
          service.store.create_session('NATIVE RECOVERY FIXTURE')
          workspace=service._workspace_path
          (workspace/'selected.txt').write_bytes(b'JARVIS NATIVE ARCHIVE 7319')
          (workspace/'artifact.bin').write_bytes(bytes(range(256)))
          (workspace/'unselected.txt').write_bytes(b'NO COPIAR')
          identity=None
          if mode=='release':
            from test_release_maintenance import verification,source
            identity=await verification(service.maintenance_coordinator,parent)
            source(parent,'v1'); source(parent,'v2')
            legacy=source(parent,'oldfour')
            purposes=['backup.create','memory.restore.copy_scope','profile.restore.activate','restore.stage']
            literal=('_PURPOSES = {'+','.join(repr(value) for value in purposes)+'}').encode()+bytes([10])
            legacy_source='python/jarvis_core/maintenance_journal.py'
            (legacy/legacy_source).write_bytes(literal)
            (legacy/legacy_source).chmod(0o600)
            legacy_manifest=json.loads((legacy/'release.json').read_bytes())
            legacy_manifest['maintenance_purposes']=purposes
            for row in legacy_manifest['files']:
              if row['path']==legacy_source:
                row['size']=len(literal); row['sha256']=hashlib.sha256(literal).hexdigest()
            (legacy/'release.json').write_bytes(json.dumps(legacy_manifest,sort_keys=True,
                ensure_ascii=False,separators=(',',':')).encode())
            (parent/'installation').mkdir(mode=0o700)
          async def trace(params):
            assert not params
            return {'provider_calls':peer.calls,'workspace':str(service._workspace_path),
                    'verification_id':identity,'memory_closed':service.memory._disk is None}
          service.server._handlers['fixture.recovery.trace']=trace
          done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
          try: await service.start(); await done.wait()
          finally:
            await service.aclose()
            if mode=='release':
              # Only our immutable synthetic release directories, after all
              # service workers settle, become removable by fixture teardown.
              for directory,_,_ in os.walk(parent/'installation'):
                os.chmod(directory,0o700)
        asyncio.run(main())
        """, directory, mode]
        let tests = URL(fileURLWithPath: source).deletingLastPathComponent().appendingPathComponent("tests").path
        child.environment = ["PATH":"/usr/bin:/bin", "PYTHONPATH":source + ":" + tests, "PYTHONNOUSERSITE":"1", "PYTHONDONTWRITEBYTECODE":"1"]
        let logPath = directory + "/fixture.log", descriptor = Darwin.open(directory + "/fixture.log", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw RPCFailure.configuration }
        let log = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true); child.standardOutput = FileHandle.nullDevice; child.standardError = log
        addTeardownBlock { try? log.close() }; try startFixtureProcess(child, directory: directory)
        let runtime = ProfileRuntimeGuard(), transport = LocalRPCClient(stateDirectory: directory + "/profile")
        let client = RecoveryExtensionClient(transport: transport, runtime: runtime)
        do { try await ServiceBootstrap.waitForHealth(stateDirectory: directory + "/profile") { child.isRunning }; try await body(client, transport, directory); await client.close() }
        catch {
            if let data = try? Data(contentsOf: URL(fileURLWithPath: logPath)), !data.isEmpty { print(String(decoding: data.prefix(8192), as: UTF8.self)) }
            await client.close(); throw error
        }
    }
    @MainActor private func settled(_ client: RecoveryExtensionClient, _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        var row = initial
        for _ in 0..<300 {
            if !row.resourcesPending { return row }; row = try await client.operation(initial.id)
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("El fixture propio no asentó la operación"); return row
    }
    @MainActor private func reviewed(_ client: RecoveryExtensionClient, _ initial: MaintenanceOperation) async throws -> RecoveryExtensionReview {
        let row = try await settled(client, initial)
        XCTAssertTrue(row.success, row.errorCode ?? row.phase)
        return try RecoveryExtensionReview(row.result["review"])
    }
    @MainActor private func perform(_ client: RecoveryExtensionClient, _ review: RecoveryExtensionReview, allow: Bool = true) async throws -> MaintenanceOperation {
        let initial = try await client.submit(review)
        // Only this synthetic profile/fixture: never a real owner's decision.
        for _ in 0..<200 {
            let pending = try await client.pending()
            if let ticket = pending.approvals.first(where: { review.matches($0, operation: initial) }) {
                let accepted = try await client.decide(ticket, review: review, operation: initial, allow: allow); XCTAssertTrue(accepted)
                let final = try await settled(client, initial); try review.validateResult(final); return final
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("No apareció la aprobación sintética exacta"); return initial
    }
    @MainActor func testActualServiceSelectedArchiveRestoresWithoutNeighborsOrAssociationChange() async throws {
        try await fixture { client, transport, parent in
            let entries = [try RecoveryWorkspaceEntry(path: "selected.txt"), try RecoveryWorkspaceEntry(path: "artifact.bin", role: "artifact")]
            let review = try await self.reviewed(client, client.workspaceBackup(entries: entries, parent: parent, name: "portable"))
            XCTAssertEqual(review.workspace?.summary.artifacts, 1)
            let created = try await self.perform(client, review)
            XCTAssertTrue(created.success); XCTAssertEqual(created.receipt?.effect, "published_verified_bytes")
            let hash = try XCTUnwrap(created.receipt?.manifestHash)
            let raw = try Data(contentsOf: URL(fileURLWithPath: parent + "/portable/manifest.json"))
            XCTAssertEqual(SHA256.hash(data: raw).map { String(format: "%02x", $0) }.joined(), hash)
            let restore = try await self.reviewed(client, client.workspaceRestore(archive: parent + "/portable", expectedHash: hash, parent: parent, name: "restored-files"))
            let restored = try await self.perform(client, restore)
            XCTAssertTrue(restored.success); XCTAssertEqual(restored.result["association_changed"], .bool(false))
            XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: parent + "/restored-files/selected.txt")), Data("JARVIS NATIVE ARCHIVE 7319".utf8))
            XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: parent + "/restored-files/artifact.bin")), Data(0...255))
            XCTAssertFalse(FileManager.default.fileExists(atPath: parent + "/restored-files/unselected.txt"))
            let denied = try await self.reviewed(client, client.workspaceBackup(entries: entries, parent: parent, name: "denied"))
            let denial = try await self.perform(client, denied, allow: false)
            XCTAssertEqual(denial.phase, "denied"); XCTAssertFalse(FileManager.default.fileExists(atPath: parent + "/denied"))
            let trace = try await transport.call("fixture.recovery.trace"); XCTAssertEqual(trace["provider_calls"], .number(0)); XCTAssertNotEqual(trace["workspace"], .string(parent + "/restored-files"))
        }
    }
    @MainActor func testActualServiceFtsRepairAndWalLossMetadataRemainExplicitAndOriginalsCold() async throws {
        for mode in ["fts", "wal"] {
            try await fixture(mode) { client, transport, parent in
                let review = try await self.reviewed(client, client.repair(role: "memory", kind: mode == "fts" ? "memory_fts_rebuild" : "wal_valid_prefix", parent: parent, name: "repaired"))
                let details = try XCTUnwrap(review.repair)
                if mode == "wal" { XCTAssertEqual(details.discardedBytes, 101); XCTAssertNil(details.derivedRows) }
                else { XCTAssertNil(details.discardedBytes); XCTAssertEqual(details.derivedRows, 1) }
                let result = try await self.perform(client, review); XCTAssertTrue(result.success)
                XCTAssertTrue(FileManager.default.fileExists(atPath: parent + "/repaired/manifest.json"))
                let trace = try await transport.call("fixture.recovery.trace"); XCTAssertEqual(trace["provider_calls"], .number(0)); XCTAssertEqual(trace["memory_closed"], .bool(true))
            }
        }
    }
    @MainActor func testActualServiceInstallUpdateRollbackAndCoherentLaunchPlanNeverRunPayload() async throws {
        try await fixture("release") { client, transport, parent in
            let trace = try await transport.call("fixture.recovery.trace"), verification = try XCTUnwrap(trace["verification_id"].string)
            for version in ["v1", "v2"] {
                let review = try await self.reviewed(client, client.release(artifact: parent + "/" + version, installation: parent + "/installation", verificationID: verification))
                XCTAssertEqual(review.release?.versionKey, version)
                let purposes = try RecoveryReleaseDetails.maintenancePurposes(review.metadata["profile_pin"]["maintenance_purposes"])
                XCTAssertTrue(purposes.contains("release.update.apply")); XCTAssertTrue(purposes.contains("release.rollback.apply")); XCTAssertTrue(purposes.contains("backup.create"))
                let result = try await self.perform(client, review); XCTAssertTrue(result.success); XCTAssertEqual(result.receipt?.effect, "current_selected")
                XCTAssertEqual(result.result["release"]["profile_pin"]["maintenance_purposes"], review.metadata["profile_pin"]["maintenance_purposes"])
                XCTAssertEqual(result.result["release"]["review_digest"], review.metadata["digest"])
                XCTAssertEqual(result.receipt?.manifestHash, review.release?.manifestSHA)
            }
            let rollback = try await self.reviewed(client, client.rollback(installation: parent + "/installation", verificationID: verification))
            XCTAssertEqual(rollback.release?.versionKey, "v1")
            let result = try await self.perform(client, rollback); XCTAssertTrue(result.success)
            XCTAssertEqual(result.result["release"]["profile_pin"]["maintenance_purposes"], rollback.metadata["profile_pin"]["maintenance_purposes"])
            let inspected = try await self.settled(client, client.inspect(installation: parent + "/installation")); XCTAssertTrue(try RecoveryInstallationSummary(inspected.result).notice.contains("v1"))
            let planned = try await self.settled(client, client.launchPlan(installation: parent + "/installation", verificationID: verification, expectedCurrent: nil))
            let plan = try RecoveryLaunchPlan(planned.result)
            XCTAssertEqual(plan.versionKey, "v1"); XCTAssertTrue(plan.nativeExecutable.contains("/releases/v1/")); XCTAssertTrue(plan.pythonSource.hasSuffix("/releases/v1/python"))
            let required = try RecoveryReleaseDetails.maintenancePurposes(planned.result["release_launch"]["profile_pin"]["maintenance_purposes"])
            XCTAssertTrue(Set(required).isSubset(of: Set(plan.maintenancePurposes)))
            let after = try await transport.call("fixture.recovery.trace"); XCTAssertEqual(after["provider_calls"], .number(0))
        }
    }
    @MainActor func testActualServiceRejectsOldPurposeCapabilitiesBeforeApprovalOrPublication() async throws {
        try await fixture("release") { client, transport, parent in
            let trace = try await transport.call("fixture.recovery.trace"), verification = try XCTUnwrap(trace["verification_id"].string)
            let initial = try await client.release(artifact: parent + "/oldfour", installation: parent + "/installation", verificationID: verification)
            let result = try await self.settled(client, initial)
            XCTAssertEqual(result.phase, "failed"); XCTAssertEqual(result.errorCode, "release_maintenance_purposes_incompatible")
            XCTAssertFalse(result.success); XCTAssertNil(result.receipt)
            let pending = try await client.pending(); XCTAssertTrue(pending.approvals.isEmpty)
            XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: parent + "/installation"), [])
            let after = try await transport.call("fixture.recovery.trace"); XCTAssertEqual(after["provider_calls"], .number(0))
        }
    }
}
