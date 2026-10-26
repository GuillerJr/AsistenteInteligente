import Foundation
import XCTest
@testable import DesktopCore

private enum WalMaintenanceFixture {
    static let operationID = String(repeating: "b", count: 32)
    static let scope: RPCValue = .object(["profile_id": .string("synthetic_profile"), "generation": .number(7)])
    static func changed(_ value: RPCValue, _ fields: [String: RPCValue]) -> RPCValue {
        .object(value.object!.merging(fields) { _, new in new })
    }
    static func job(kind: String = "doctor.report", phase: String = "cleanup_pending", worker: Bool = false, owner: Bool = true, historicCleanup: Bool = false) -> RPCValue {
        let effect = kind == "backup.create"
        let receipt: RPCValue = historicCleanup ? .object([
            "operation_id": .string(operationID), "purpose": .string(kind),
            "status": .string("failed"), "effect_status": .string("no_published_effect"),
            "target_name": .string("jarvis-copia"), "artifact_id": .null,
            "manifest_sha256": .null, "target_identity": .null, "new_profile_id": .null,
            "error_code": .string("profile_cold_wal_cleanup_pending"),
            "cleanup_error_code": .string("profile_cold_wal_cleanup_pending"), "cleanup_pending": .bool(true)
        ]) : .null
        return .object([
            "operation_id": .string(operationID), "kind": .string(kind), "scope": scope,
            "phase": .string(phase), "stage": .null, "result": .null,
            "error_code": .string("profile_cold_wal_cleanup_pending"),
            "review_id": effect ? MaintenanceFixtures.review["review_id"] : .null,
            "review_digest": effect ? MaintenanceFixtures.review["review_digest"] : .null,
            "receipt": receipt, "receipt_durable": .bool(effect && historicCleanup),
            "worker_pending": .bool(worker), "cleanup_owner_pending": .bool(owner),
            "cancellation_requested": .bool(false)
        ])
    }
    static func status(_ rows: [RPCValue]) -> RPCValue {
        .object(["profile_id": .string("synthetic_profile"), "generation": .number(7),
                 "closing": .bool(false), "operations": .array(rows), "previous_operations": .array([]),
                 "blockers": .array(rows.contains { $0["cleanup_owner_pending"] == .bool(true) } ? [.string("profile_maintenance_active")] : [])])
    }
    static let pending: RPCValue = .object(["profile_id": .string("synthetic_profile"), "generation": .number(7), "pending": .array([])])
    static func walReview(recoveries: [RPCValue] = [.object(["role": .string("memory"), "mode": .string("cold_wal"), "semantics": .string("confirmed_transactions_only")])], kind: String = "backup.create") throws -> RPCValue {
        var metadata = MaintenanceFixtures.review["metadata"].object!
        var sources = metadata["source"]!.array!
        sources[1] = changed(sources[1], ["disposition": .string("present")])
        metadata["source"] = .array(sources); metadata["sqlite_recovery"] = .array(recoveries)
        metadata["kind"] = .string(kind)
        let value = RPCValue.object(metadata)
        return changed(MaintenanceFixtures.review, ["metadata": value,
            "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(value)))])
    }
}

private actor WalMaintenancePeer {
    var rows: [RPCValue] = []
    var calls: [(String, [String: RPCValue])] = []
    let initial: RPCValue
    var nextCleanup: RPCValue
    var doctorFailure: RPCFailure?
    var cleanupFailure: RPCFailure?
    var startOverride: RPCValue?
    init(kind: String = "doctor.report", doctorFailure: RPCFailure? = nil, cleanupFailure: RPCFailure? = nil) {
        initial = WalMaintenanceFixture.job(kind: kind, historicCleanup: kind == "backup.create")
        nextCleanup = WalMaintenanceFixture.job(kind: kind, phase: "failed", owner: false, historicCleanup: kind == "backup.create")
        self.doctorFailure = doctorFailure; self.cleanupFailure = cleanupFailure
    }
    func call(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        calls.append((method, params))
        switch method {
        case "maintenance.status": return WalMaintenanceFixture.status(rows)
        case "maintenance.pending": return WalMaintenanceFixture.pending
        case "backup.review": return MaintenanceFixtures.review
        case "doctor.report", "backup.create":
            rows = [startOverride ?? initial]
            if let doctorFailure { throw doctorFailure }
            return rows[0]
        case "maintenance.cleanup", "maintenance.cancel":
            if let cleanupFailure { throw cleanupFailure }
            rows = [nextCleanup]; return nextCleanup
        default: throw RPCFailure.configuration
        }
    }
    func forgetOperations() { rows = [] }
    func setRows(_ rows: [RPCValue]) { self.rows = rows }
    func setCleanup(_ value: RPCValue) { nextCleanup = value }
    func setStart(_ value: RPCValue) { startOverride = value }
    func recorded() -> [(String, [String: RPCValue])] { calls }
}

final class WalMaintenanceOwnershipTests: XCTestCase {
    func testWorkerSettlementDoesNotRetireWalCleanupOwnerOrResumeMonitor() throws {
        for kind in ["doctor.report", "backup.create"] {
            let job = try MaintenanceOperation(WalMaintenanceFixture.job(kind: kind, historicCleanup: kind == "backup.create"))
            XCTAssertTrue(job.settled)
            XCTAssertTrue(job.resourcesPending)
            XCTAssertFalse(job.success)
            XCTAssertTrue(MaintenanceMonitorPolicy.pauses(visible: false, operation: job, requestInFlight: false, unresolvedResponse: false))
        }
    }

    func testHistoricReceiptCleanupFlagDoesNotInventCurrentCustody() throws {
        let job = try MaintenanceOperation(WalMaintenanceFixture.job(kind: "backup.create", phase: "failed", owner: false, historicCleanup: true))
        XCTAssertTrue(job.receipt?.cleanupPending == true)
        XCTAssertFalse(job.cleanupOwnerPending)
        XCTAssertFalse(job.resourcesPending)
        XCTAssertFalse(job.success)
        XCTAssertFalse(MaintenanceMonitorPolicy.pauses(visible: false, operation: job, requestInFlight: false, unresolvedResponse: false))
    }

    func testCleanupPhaseWithoutOwnerFlagStillBlocksUntilItsStateChanges() throws {
        let legacy = try MaintenanceOperation(WalMaintenanceFixture.job(owner: false))
        XCTAssertTrue(legacy.settled)
        XCTAssertFalse(legacy.cleanupOwnerPending)
        XCTAssertTrue(legacy.resourcesPending)
        XCTAssertFalse(legacy.success)
        XCTAssertTrue(MaintenanceMonitorPolicy.pauses(visible: false, operation: legacy, requestInFlight: false, unresolvedResponse: false))
        let finished = try MaintenanceOperation(WalMaintenanceFixture.job(phase: "failed", owner: false))
        XCTAssertFalse(finished.resourcesPending)
    }

    func testCompletedDoctorCannotClaimSuccessWhileRetainingCleanupOwner() throws {
        let result: RPCValue = .object(["report": .object([
            "report_id": .string(String(repeating: "c", count: 32)),
            "profile_id": .string("synthetic_profile"), "generation": .number(7),
            "status": .string("ok"), "coherence": .string("coherent_snapshot"),
            "profile_identity": .object(["device": .string("1"), "inode": .string("2")]),
            "checks": .array([]), "blockers": .array([]), "suggestions": .array([]),
            "supported_repair_actions": .array([]), "effect": .string("read_only_diagnostic")
        ])])
        let contradictory = WalMaintenanceFixture.changed(WalMaintenanceFixture.job(phase: "complete"), ["result": result])
        XCTAssertThrowsError(try MaintenanceOperation(contradictory))
        let retired = WalMaintenanceFixture.changed(contradictory, ["cleanup_owner_pending": .bool(false)])
        XCTAssertTrue(try MaintenanceOperation(retired).success)
    }

    @MainActor func testDoctorCleanupSurvivesClosingPanelAndBlocksEveryNewRead() async throws {
        let peer = WalMaintenancePeer()
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        let view = UUID(); store.setVisible(view, visible: true)
        await store.runDoctor()
        store.setVisible(view, visible: false)
        XCTAssertEqual(store.operation?.kind, "doctor.report")
        XCTAssertTrue(store.operation?.cleanupOwnerPending == true)
        XCTAssertTrue(store.readAwaitingSettlement)
        XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.monitorPaused)
        XCTAssertFalse(store.mayPrepare); XCTAssertTrue(store.canCleanup)
        let before = await peer.recorded().count
        await store.runDoctor(); await store.verify(path: "/private/tmp/synthetic-backup", expectedHash: "")
        await store.prepareBackup(parent: "/private/tmp/prueba-Ω", name: "jarvis-copia")
        let after = await peer.recorded()
        XCTAssertEqual(after.count, before)
        await store.close()
    }

    @MainActor func testExplicitCleanupUsesOnlyExactOwnerAndKeepsReadFailure() async throws {
        let peer = WalMaintenancePeer()
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        await store.runDoctor(); await store.cleanup(); await store.refresh()
        XCTAssertFalse(store.operation?.cleanupOwnerPending == true)
        XCTAssertEqual(store.operation?.phase, "failed")
        XCTAssertFalse(store.operation?.success == true)
        XCTAssertNil(store.doctor)
        XCTAssertFalse(store.readAwaitingSettlement); XCTAssertFalse(store.blocksInteraction)
        XCTAssertTrue(store.mayPrepare)
        let calls = await peer.recorded()
        XCTAssertEqual(calls.filter { $0.0 == "maintenance.cleanup" }.map(\.1), [["operation_id": .string(WalMaintenanceFixture.operationID)]])
        XCTAssertEqual(calls.filter { $0.0 == "doctor.report" }.count, 1)
        XCTAssertFalse(calls.contains { ["backup.create", "maintenance.decide"].contains($0.0) })
        await store.close()
    }

    @MainActor func testBackupCleanupPreservesHistoricalReceiptWithoutBlockingForever() async throws {
        let peer = WalMaintenancePeer(kind: "backup.create")
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        let view = UUID(); store.setVisible(view, visible: true)
        await store.prepareBackup(parent: "/private/tmp/prueba-Ω", name: "jarvis-copia")
        await store.submitReview()
        XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.canCleanup)
        let before = store.operation?.receipt
        await store.cleanup(); await store.refresh()
        XCTAssertTrue(store.operation?.receipt?.cleanupPending == true)
        XCTAssertEqual(before?.status, store.operation?.receipt?.status)
        XCTAssertEqual(store.operation?.phase, "failed"); XCTAssertFalse(store.operation?.resourcesPending == true)
        XCTAssertFalse(store.blocksInteraction); XCTAssertTrue(store.mayPrepare)
        let calls = await peer.recorded()
        XCTAssertEqual(calls.filter { $0.0 == "backup.create" }.count, 1)
        XCTAssertEqual(calls.filter { $0.0 == "maintenance.cleanup" }.count, 1)
        await store.close()
    }

    @MainActor func testLostReadAcknowledgementCannotBeDismissedOrResubmitted() async throws {
        let peer = WalMaintenancePeer(doctorFailure: .disconnected)
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        await store.runDoctor()
        XCTAssertNil(store.operation); XCTAssertTrue(store.readAwaitingSettlement)
        XCTAssertTrue(store.blocksInteraction); XCTAssertFalse(store.mayPrepare)
        store.acknowledgeUnconfirmedLocally(); store.setVisible(UUID(), visible: false)
        await store.refresh(); await store.runDoctor()
        XCTAssertTrue(store.readAwaitingSettlement); XCTAssertTrue(store.blocksInteraction)
        XCTAssertNil(store.operation) // A different status row is not a correlated ACK.
        let calls = await peer.recorded()
        XCTAssertEqual(calls.filter { $0.0 == "doctor.report" }.count, 1)
        XCTAssertFalse(calls.contains { $0.0 == "maintenance.cleanup" })
        await store.close()
    }

    @MainActor func testStatusCannotDiscardOrAutomaticallyCleanAnUnsettledOwner() async throws {
        let peer = WalMaintenancePeer()
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        await store.runDoctor(); await peer.forgetOperations(); await store.refresh()
        XCTAssertEqual(store.operation?.id, WalMaintenanceFixture.operationID)
        XCTAssertTrue(store.operation?.cleanupOwnerPending == true)
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertTrue(store.blocksInteraction)
        let calls = await peer.recorded(); XCTAssertFalse(calls.contains { $0.0 == "maintenance.cleanup" })
        await store.close()
    }

    @MainActor func testFailedCleanupAndReconfigurationRetainSameOwner() async throws {
        let peer = WalMaintenancePeer(cleanupFailure: .disconnected)
        let other = WalMaintenancePeer()
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        await store.runDoctor(); await store.cleanup()
        store.configure(client: MaintenanceClient(request: { try await other.call($0, $1) }))
        XCTAssertEqual(store.operation?.id, WalMaintenanceFixture.operationID)
        XCTAssertTrue(store.operation?.cleanupOwnerPending == true)
        XCTAssertTrue(store.readAwaitingSettlement); XCTAssertTrue(store.blocksInteraction)
        await store.refresh()
        let otherCalls = await other.recorded()
        XCTAssertTrue(otherCalls.isEmpty)
        let calls = await peer.recorded(); XCTAssertEqual(calls.filter { $0.0 == "maintenance.cleanup" }.count, 1)
        await store.close()
    }

    func testCleanupCannotChangeIdentityOrRunWhileWorkerIsActive() async throws {
        let peer = WalMaintenancePeer()
        let client = MaintenanceClient(request: { try await peer.call($0, $1) })
        let active = try MaintenanceOperation(WalMaintenanceFixture.job(worker: true))
        do { _ = try await client.cleanup(active); XCTFail("cleaned live worker") } catch { }
        let before = await peer.recorded()
        XCTAssertTrue(before.isEmpty)
        let wrong = WalMaintenanceFixture.changed(WalMaintenanceFixture.job(phase: "failed", owner: false), ["operation_id": .string(String(repeating: "f", count: 32))])
        await peer.setCleanup(wrong)
        do { _ = try await client.cleanup(MaintenanceOperation(WalMaintenanceFixture.job())); XCTFail("accepted another operation") } catch { }
        let after = await peer.recorded()
        XCTAssertEqual(after.count, 1)
    }

    @MainActor func testCancelAcknowledgementCannotReplaceCapturedReadKindOrReleaseCustody() async throws {
        let peer = WalMaintenancePeer()
        await peer.setStart(WalMaintenanceFixture.job(phase: "working", worker: true, owner: false))
        let store = MaintenanceStore(maxPolls: 0)
        store.configure(client: MaintenanceClient(request: { try await peer.call($0, $1) }))
        await store.runDoctor()
        await peer.setCleanup(WalMaintenanceFixture.job(kind: "backup.create", phase: "failed", owner: false, historicCleanup: true))
        await store.cancel()
        XCTAssertEqual(store.operation?.kind, "doctor.report")
        XCTAssertEqual(store.operation?.phase, "working")
        XCTAssertTrue(store.operation?.workerPending == true)
        XCTAssertTrue(store.readAwaitingSettlement); XCTAssertTrue(store.blocksInteraction)
        XCTAssertNotNil(store.errorMessage)
        let calls = await peer.recorded()
        XCTAssertEqual(calls.filter { $0.0 == "maintenance.cancel" }.count, 1)
        await store.close()
    }

    func testWalReviewBindsExactRecoverySemanticsAndSupportsValidatedColdMemoryDTO() throws {
        let review = try MaintenanceReview(WalMaintenanceFixture.walReview())
        XCTAssertEqual(review.sqliteRecovery.map(\.role), ["memory"])
        let valid: RPCValue = .object(["role": .string("memory"), "mode": .string("cold_wal"), "semantics": .string("confirmed_transactions_only")])
        for rows in [[valid, valid], [WalMaintenanceFixture.changed(valid, ["role": .string("telegram")])],
                     [WalMaintenanceFixture.changed(valid, ["mode": .string("cold_delete")])],
                     [WalMaintenanceFixture.changed(valid, ["semantics": .string("all_frames")])]] {
            XCTAssertThrowsError(try MaintenanceReview(WalMaintenanceFixture.walReview(recoveries: rows)))
        }
        XCTAssertThrowsError(try MaintenanceReview(WalMaintenanceFixture.walReview(kind: "restore.stage")))
        let walMemory = WalMaintenanceFixture.changed(RestoredMemoryFixture.inventory(), ["source_kind": .string("cold_wal_snapshot")])
        let inventory = try RestoredMemoryInventory(walMemory)
        XCTAssertEqual(inventory.sourceKind, "cold_wal_snapshot")
        XCTAssertEqual(inventory.schemaVersion, 1)
    }

    func testWalErrorsAreLocalizedAndNeverExposeRemoteDetails() {
        for code in ["profile_cold_wal_invalid", "profile_cold_wal_no_commit", "profile_cold_wal_size_limit", "profile_cold_wal_work_limit", "profile_cold_wal_cleanup_pending", "profile_cold_wal_changed", "profile_cold_wal_unavailable", "profile_cold_wal_sqlite_unavailable"] {
            XCTAssertNotNil(MaintenancePresentation.operationError(code))
            XCTAssertFalse(MaintenancePresentation.error(RPCFailure.remote(-32040, code)).contains(code))
        }
        XCTAssertNil(MaintenancePresentation.operationError("PRIVATE WAL PATH TOKEN"))
        XCTAssertFalse(MaintenancePresentation.error(RPCFailure.remote(-32040, "PRIVATE WAL PATH TOKEN")).contains("PRIVATE"))
    }
}
