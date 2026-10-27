import Foundation
import XCTest
@testable import DesktopCore

/// Actual fixture services and IPC. Faults and authorization decisions are
/// synthetic; no owner account, installed app, or personal profile participates.
final class RecoveryNegativeIntegrationTests: XCTestCase {
    @MainActor private struct Running {
        let parent: String
        let root: String
        let profile: String
        let transport: LocalRPCClient
        let maintenance: MaintenanceClient
        let recovery: RecoveryExtensionClient
        let lifecycle: FixtureProcess
    }

    @MainActor private func start(_ identity: String = "wal_absent_all", negative: String? = nil,
                                  restart: Running? = nil) async throws -> Running {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para el fixture IPC propio.")
        }
        let parent = restart?.parent ?? "/private/tmp/jarvis-ng-" + String(UUID().uuidString.prefix(6))
        let logs = restart == nil ? parent : "/private/tmp/jarvis-nr-" + String(UUID().uuidString.prefix(6))
        try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let repository = URL(fileURLWithPath: source).deletingLastPathComponent()
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio,sys
        from pathlib import Path
        import recovery_qualification_matrix as matrix
        parent=Path(sys.argv[1]); identity=sys.argv[2]
        if sys.argv[4]=='1': fixture=parent/identity/'fixture.json'
        else:
          specification=next(row for row in matrix.cases() if row['id']==identity)
          fixture=Path(matrix.prepare_case(parent,specification)['fixture'])
        asyncio.run(matrix.serve_fixture(fixture,negative=None if sys.argv[3]=='none' else sys.argv[3]))
        """, parent, identity, negative ?? "none", restart == nil ? "0" : "1"]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source + ":" + repository.appendingPathComponent("tests").path + ":" + repository.appendingPathComponent("script").path,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        let log = FileHandle(forWritingAtPath: logs + "/fixture.log")
        if let log { child.standardError = log; addTeardownBlock { try? log.close() } }
        else {
            _ = FileManager.default.createFile(atPath: logs + "/fixture.log", contents: nil,
                                           attributes: [.posixPermissions: 0o600])
            let created = try XCTUnwrap(FileHandle(forWritingAtPath: logs + "/fixture.log"))
            child.standardError = created
            addTeardownBlock { try? created.close() }
        }
        child.standardOutput = FileHandle.nullDevice
        let lifecycle = try startFixtureProcess(child, directory: logs)
        let root = parent + "/" + identity
        let profile = root + "/profile"
        let transport = LocalRPCClient(stateDirectory: profile)
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: profile) { child.isRunning }
            _ = try await RecoveryBootstrap.inspect { method, params in
                try await transport.call(method, params: params)
            }
            let trace = try await transport.call("fixture.matrix.trace")
            XCTAssertEqual(trace["source_unchanged"], .bool(true))
            XCTAssertEqual(trace["stores_closed"], .bool(true))
            XCTAssertEqual(trace["provider_calls"], .number(0))
        } catch {
            if let data = try? Data(contentsOf: URL(fileURLWithPath: logs + "/fixture.log")) {
                print(String(decoding: data.prefix(8192), as: UTF8.self))
            }
            throw error
        }
        return Running(parent: parent, root: root, profile: profile, transport: transport,
                       maintenance: MaintenanceClient(transport: transport),
                       recovery: RecoveryExtensionClient(transport: transport, runtime: ProfileRuntimeGuard()),
                       lifecycle: lifecycle)
    }

    @MainActor private func stop(_ running: Running) async throws {
        let trace = try await running.transport.call("fixture.matrix.trace")
        XCTAssertEqual(trace["source_unchanged"], .bool(true))
        XCTAssertEqual(trace["stores_closed"], .bool(true))
        XCTAssertEqual(trace["provider_calls"], .number(0))
        await running.maintenance.close()
        let stopped = await running.lifecycle.stop()
        XCTAssertEqual(stopped, .stopped)
    }

    @MainActor private func settled(_ running: Running,
                                   _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        var row = initial
        for _ in 0..<4000 {
            if !row.resourcesPending { return row }
            try await Task.sleep(for: .milliseconds(10))
            row = try await running.maintenance.operation(initial.id)
        }
        throw RPCFailure.unavailable
    }

    @MainActor private func ticket(_ running: Running, review: MaintenanceReview,
                                  operation: MaintenanceOperation) async throws -> MaintenanceApproval {
        for _ in 0..<300 {
            let pending = try await running.maintenance.pending()
            if let value = pending.approvals.first(where: { $0.matches(review, operation: operation) }) {
                return value
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor private func decide(_ running: Running, review: MaintenanceReview,
                                  operation: MaintenanceOperation, allow: Bool = true) async throws {
        let approval = try await ticket(running, review: review, operation: operation)
        let accepted = try await running.maintenance.decide(approval, review: review,
                                                           operation: operation, allow: allow)
        XCTAssertTrue(accepted) // Synthetic fixture authorization only.
    }

    @MainActor func testActualIpcRejectsNoCommitCorruptionMixedFamilyAndBadJournalChecksum() async throws {
        for (identity, negative) in [("prefix_checksum_memory", "no_commit"),
                                     ("wal_header_all", "ambiguous"),
                                     ("journal_hot_delete_all", "journal_checksum")] {
            let running = try await start(identity, negative: negative)
            if negative == "no_commit" {
                let initial = try await running.recovery.repair(role: "memory", kind: "wal_valid_prefix",
                                                                parent: running.root, name: "reparado")
                let failure = try await settled(running, initial)
                XCTAssertEqual(failure.phase, "failed")
                XCTAssertEqual(failure.errorCode, "profile_cold_wal_invalid")
            } else if negative == "ambiguous" {
                do {
                    _ = try await running.maintenance.reviewBackup(parent: running.root, name: "respaldo")
                    XCTFail("La familia mixta debe rechazarse antes de preparar una autorización")
                } catch let error as RPCFailure {
                    XCTAssertEqual(error, .remote(-32040, "profile_lazy_source_unavailable"))
                }
            } else {
                let review = try await running.maintenance.reviewBackup(parent: running.root, name: "respaldo")
                let operation = try await running.maintenance.submit(review)
                try await decide(running, review: review, operation: operation)
                let failure = try await settled(running, operation)
                XCTAssertEqual(failure.phase, "failed")
                XCTAssertEqual(failure.errorCode, "profile_cold_journal_checksum_invalid")
                XCTAssertTrue(failure.receiptDurable)
                XCTAssertEqual(failure.receipt?.effect, "no_published_effect")
            }
            XCTAssertFalse(FileManager.default.fileExists(atPath: running.root + "/respaldo"))
            XCTAssertFalse(FileManager.default.fileExists(atPath: running.root + "/reparado"))
            let pending = try await running.maintenance.pending()
            XCTAssertTrue(pending.approvals.isEmpty)
            try await stop(running)
        }
    }

    @MainActor func testActualIpcDenyCancelAndExpiredEffectDoNotPublishOrReplay() async throws {
        for mode in ["deny", "cancel", "expiry", "ttl"] {
            let running = try await start()
            if mode == "ttl" {
                _ = try await running.transport.call("fixture.matrix.control", params: ["action": .string("short_ttl")])
            }
            let review = try await running.maintenance.reviewBackup(parent: running.root, name: "respaldo")
            let operation = try await running.maintenance.submit(review)
            if mode != "ttl" { _ = try await ticket(running, review: review, operation: operation) }
            if mode == "cancel" { _ = try await running.maintenance.cancel(operation.id) }
            else if mode != "ttl" {
                if mode == "expiry" {
                    _ = try await running.transport.call("fixture.matrix.control", params: ["action": .string("advance_clock")])
                }
                try await decide(running, review: review, operation: operation, allow: mode != "deny")
            }
            let result = try await settled(running, operation)
            XCTAssertEqual(result.phase, ["deny", "ttl"].contains(mode) ? "denied" : "cancelled")
            XCTAssertTrue(result.receiptDurable)
            if mode == "expiry" {
                XCTAssertEqual(result.errorCode, "recovery_deadline_exceeded")
                XCTAssertEqual(result.receipt?.effect, "no_published_effect")
            }
            XCTAssertFalse(result.cleanupOwnerPending)
            XCTAssertFalse(FileManager.default.fileExists(atPath: running.root + "/respaldo"))
            let replay = try await running.maintenance.submit(review)
            XCTAssertEqual(replay.id, result.id)
            XCTAssertEqual(replay.phase, result.phase)
            let pending = try await running.maintenance.pending()
            XCTAssertTrue(pending.approvals.isEmpty)
            try await stop(running)
        }
    }

    @MainActor func testActualWorkerCrashRestartKeepsInterruptedUnknownAndNeverReplays() async throws {
        let first = try await start()
        _ = try await first.transport.call("fixture.matrix.control", params: ["action": .string("block_before_publish")])
        let review = try await first.maintenance.reviewBackup(parent: first.root, name: "respaldo")
        let operation = try await first.maintenance.submit(review)
        try await decide(first, review: review, operation: operation)
        var blocked = false
        for _ in 0..<600 {
            let row = try await first.transport.call("fixture.matrix.control", params: ["action": .string("blocked")])
            if row["blocked"] == .bool(true) { blocked = true; break }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(blocked)
        let working = try await first.maintenance.operation(operation.id)
        XCTAssertEqual(working.phase, "working")
        XCTAssertTrue(working.workerPending)
        let beforeCrash = try await first.transport.call("fixture.matrix.trace")
        let oldSocket = beforeCrash["socket_binding"]
        let stage = first.root + "/.jarvis-stage-" + operation.id
        let retainedManifest = try Data(contentsOf: URL(fileURLWithPath: stage + "/manifest.json"))
        do {
            _ = try await first.transport.call("fixture.matrix.control", params: ["action": .string("crash")])
            XCTFail("El proceso de fixture debe terminar antes de responder")
        } catch { /* Actual owned child exits while its real worker is blocked. */ }
        await first.maintenance.close()
        let exited = await first.lifecycle.stop()
        XCTAssertEqual(exited, .stopped)
        // The owned child has actually exited. Product startup now acquires
        // the exact profile lease and can reclaim its refused socket. No test
        // helper unlinks the endpoint or manufactures a successful old receipt.
        let restarted = try await start(restart: first)
        let status = try await restarted.maintenance.status()
        XCTAssertTrue(status.operations.isEmpty)
        XCTAssertEqual(status.previousCount, 1)
        let trace = try await restarted.transport.call("fixture.matrix.trace")
        XCTAssertEqual(trace["production_socket_recovered"], .bool(true))
        XCTAssertEqual(trace["socket_binding"]["type"], .string("socket"))
        XCTAssertNotEqual(trace["socket_binding"], oldSocket)
        let previous = try XCTUnwrap(trace["previous_operations"].array?.first)
        XCTAssertEqual(previous["operation_id"], .string(operation.id))
        XCTAssertEqual(previous["phase"], .string("interrupted_unknown"))
        XCTAssertEqual(previous["receipt"], .null)
        XCTAssertFalse(FileManager.default.fileExists(atPath: restarted.root + "/respaldo"))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: stage + "/manifest.json")), retainedManifest)
        do {
            _ = try await restarted.maintenance.submit(review)
            XCTFail("No se debe reconstruir ni repetir una revisión del host terminado")
        } catch let error as RPCFailure {
            if case .remote(-32040, _) = error {} else { XCTFail("Se esperaba rechazo del host nuevo") }
        }
        let pending = try await restarted.maintenance.pending()
        XCTAssertTrue(pending.approvals.isEmpty)
        try await stop(restarted)
    }
}
