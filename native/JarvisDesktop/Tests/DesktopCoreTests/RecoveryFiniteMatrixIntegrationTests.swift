import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Actual native clients/bootstrap -> authenticated IPC -> own cold fixtures.
/// The sixteen cases use synthetic decisions and never an actual owner's UI.
final class RecoveryFiniteMatrixIntegrationTests: XCTestCase {
    @MainActor private func fixture(
        _ identity: String,
        _ body: (MaintenanceClient, RecoveryExtensionClient, LocalRPCClient, String) async throws -> Void
    ) async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para los fixtures propios de recuperación.")
        }
        let directory = "/private/tmp/jarvis-mx-" + String(UUID().uuidString.prefix(6))
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let repository = URL(fileURLWithPath: source).deletingLastPathComponent()
        let tests = repository.appendingPathComponent("tests").path
        let script = repository.appendingPathComponent("script").path
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-c", """
        import asyncio,sys
        from pathlib import Path
        import recovery_qualification_matrix as matrix
        parent=Path(sys.argv[1]); identity=sys.argv[2]
        specification=next(row for row in matrix.cases() if row['id']==identity)
        entry=matrix.prepare_case(parent,specification)
        asyncio.run(matrix.serve_fixture(Path(entry['fixture'])))
        """, directory, identity]
        process.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source + ":" + tests + ":" + script,
                               "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        let logPath = directory + "/fixture.log"
        let descriptor = Darwin.open(logPath, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw RPCFailure.configuration }
        let log = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        process.standardOutput = FileHandle.nullDevice
        process.standardError = log
        addTeardownBlock { try? log.close() }
        let lifecycle = try startFixtureProcess(process, directory: directory)
        let profile = directory + "/" + identity + "/profile"
        let transport = LocalRPCClient(stateDirectory: profile)
        let maintenance = MaintenanceClient(transport: transport)
        let recovery = RecoveryExtensionClient(transport: transport, runtime: ProfileRuntimeGuard())
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: profile) { process.isRunning }
            let before = try await transport.call("fixture.matrix.trace")
            XCTAssertEqual(before["source_unchanged"], .bool(true), identity)
            XCTAssertEqual(before["stores_closed"], .bool(true), identity)
            XCTAssertEqual(before["provider_calls"], .number(0), identity)
            // Same safe bootstrap used by the actual --recovery-only native app.
            _ = try await RecoveryBootstrap.inspect { method, params in
                try await transport.call(method, params: params)
            }
            let bootstrapped = try await transport.call("fixture.matrix.trace")
            XCTAssertEqual(bootstrapped["source_unchanged"], .bool(true), identity)
            XCTAssertEqual(bootstrapped["stores_closed"], .bool(true), identity)
            XCTAssertEqual(bootstrapped["provider_calls"], .number(0), identity)
            try await body(maintenance, recovery, transport, directory + "/" + identity)
            _ = try await RecoveryBootstrap.inspect { method, params in
                try await transport.call(method, params: params)
            }
            let after = try await transport.call("fixture.matrix.trace")
            XCTAssertEqual(after["source_unchanged"], .bool(true), identity)
            XCTAssertEqual(after["stores_closed"], .bool(true), identity)
            XCTAssertEqual(after["provider_calls"], .number(0), identity)
            await maintenance.close()
            let stopped = await lifecycle.stop()
            XCTAssertEqual(stopped, .stopped, identity)
        } catch {
            if let data = try? Data(contentsOf: URL(fileURLWithPath: logPath)), !data.isEmpty {
                print(String(decoding: data.prefix(8192), as: UTF8.self))
            }
            await maintenance.close()
            _ = await lifecycle.stop()
            throw error
        }
    }

    @MainActor private func settled(_ client: MaintenanceClient,
                                   _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        var row = initial
        for _ in 0..<6000 {
            if !row.resourcesPending { return row }
            try await Task.sleep(for: .milliseconds(10))
            row = try await client.operation(initial.id)
        }
        throw RPCFailure.unavailable
    }

    @MainActor private func allow(_ client: MaintenanceClient, review: MaintenanceReview,
                                 operation: MaintenanceOperation) async throws {
        for _ in 0..<300 {
            let pending = try await client.pending()
            if let ticket = pending.approvals.first(where: { $0.matches(review, operation: operation) }) {
                // This is a marked synthetic IPC fixture, never a real owner decision.
                let accepted = try await client.decide(ticket, review: review,
                                                       operation: operation, allow: true)
                XCTAssertTrue(accepted)
                return
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor private func allow(_ client: RecoveryExtensionClient, review: RecoveryExtensionReview,
                                 operation: MaintenanceOperation) async throws {
        for _ in 0..<300 {
            let pending = try await client.pending()
            if let ticket = pending.approvals.first(where: { review.matches($0, operation: operation) }) {
                let accepted = try await client.decide(ticket, review: review,
                                                       operation: operation, allow: true)
                XCTAssertTrue(accepted)
                return
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor private func verified(_ transport: LocalRPCClient, root: String, target: String,
                                    operation: MaintenanceOperation) async throws {
        XCTAssertTrue(operation.success, operation.errorCode ?? operation.phase)
        XCTAssertTrue(operation.receiptDurable)
        XCTAssertFalse(operation.workerPending)
        XCTAssertFalse(operation.cleanupOwnerPending)
        let result = try await transport.call("fixture.matrix.verify", params: [
            "target": .string(target), "operation_id": .string(operation.id)
        ])
        XCTAssertEqual(result["source_unchanged"], .bool(true))
        XCTAssertEqual(result["artifact_integrity"], .bool(true))
        XCTAssertEqual(result["logical_roles"], .array([.string("memory"), .string("schedules"), .string("state")]))
        XCTAssertEqual(result["owner_decision_verified_by_this_script"], .bool(false))
        let name = target == "backup" ? "respaldo" : "reparado"
        let manifest = try Data(contentsOf: URL(fileURLWithPath: root + "/" + name + "/manifest.json"))
        let hash = SHA256.hash(data: manifest).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(hash, operation.receipt?.manifestHash)
    }

    @MainActor private func backup(_ identity: String, mode: String) async throws {
        try await fixture(identity) { maintenance, _, transport, root in
            let diagnosis = try await self.settled(maintenance, maintenance.doctor())
            XCTAssertTrue(diagnosis.success, diagnosis.errorCode ?? diagnosis.phase)
            let review = try await maintenance.reviewBackup(parent: root, name: "respaldo")
            XCTAssertEqual(Set(review.sqliteRecovery.map(\.role)), Set(["state", "memory", "schedules"]))
            XCTAssertTrue(review.sqliteRecovery.allSatisfy { $0.mode == mode })
            let operation = try await maintenance.submit(review)
            try await self.allow(maintenance, review: review, operation: operation)
            let result = try await self.settled(maintenance, operation)
            try await self.verified(transport, root: root, target: "backup", operation: result)
            let verification = try await self.settled(maintenance, maintenance.verify(
                path: root + "/respaldo", expectedHash: result.receipt?.manifestHash))
            XCTAssertTrue(verification.success)
        }
    }

    @MainActor func testActualIpcAllRolesWalAbsentEmptyHeaderAndSafeBootstrap() async throws {
        for (identity, mode) in [("wal_absent_all", "cold_wal_main_only"),
                                 ("wal_empty_all", "cold_wal_empty"),
                                 ("wal_header_all", "cold_wal_header")] {
            try await backup(identity, mode: mode)
        }
    }

    @MainActor func testActualIpcAllRolesHotDeleteAndPersistJournals() async throws {
        for identity in ["journal_hot_delete_all", "journal_hot_persist_all"] {
            try await backup(identity, mode: "cold_journal_hot")
        }
    }

    @MainActor func testActualIpcAllRolesInactivePersistAndTruncateJournals() async throws {
        for identity in ["journal_inactive_persist_all", "journal_inactive_truncate_all"] {
            try await backup(identity, mode: "cold_journal_inactive")
        }
    }

    @MainActor func testActualIpcThreeTailsAcrossThreeRolesAreExplicitLossCandidates() async throws {
        for tail in ["truncated", "checksum", "old_generation"] {
            for role in ["state", "memory", "schedules"] {
                try await fixture("prefix_" + tail + "_" + role) { maintenance, recovery, transport, root in
                    let strict = try await self.settled(maintenance, maintenance.doctor())
                    XCTAssertEqual(strict.phase, "failed")
                    XCTAssertEqual(strict.errorCode, "profile_cold_wal_invalid")
                    let initial = try await recovery.repair(role: role, kind: "wal_valid_prefix",
                                                             parent: root, name: "reparado")
                    let prepared = try await self.settled(maintenance, initial)
                    XCTAssertTrue(prepared.success, prepared.errorCode ?? prepared.phase)
                    let review = try RecoveryExtensionReview(prepared.result["review"])
                    let details = try XCTUnwrap(review.repair)
                    XCTAssertEqual(details.role, role)
                    XCTAssertEqual(details.discardedBytes, tail == "truncated" ? 4119 : 4120)
                    XCTAssertEqual(details.discardedFrameSlots, 1)
                    let operation = try await recovery.submit(review)
                    try await self.allow(recovery, review: review, operation: operation)
                    let result = try await self.settled(maintenance, operation)
                    try await self.verified(transport, root: root, target: "repaired", operation: result)
                }
            }
        }
    }
}
