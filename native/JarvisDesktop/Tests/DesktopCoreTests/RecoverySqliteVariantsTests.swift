import Foundation
import XCTest
@testable import DesktopCore

final class RecoverySqliteVariantsTests: XCTestCase {
    func testNamespacedStagesRemainDistinctFromErrorCodes() throws {
        XCTAssertEqual(try MaintenanceValidation.stage(.string("release.verify")), "release.verify")
        XCTAssertEqual(try MaintenanceValidation.stage(.string("release.launch_plan_complete")), "release.launch_plan_complete")
        XCTAssertThrowsError(try MaintenanceValidation.code(.string("release.verify")))
        XCTAssertThrowsError(try MaintenanceValidation.stage(.string("release/secret")))
        XCTAssertThrowsError(try MaintenanceValidation.stage(.string("release\nverify")))
    }
    func testDistinctSqliteRecoveryModesRequireTheirActualSemantics() throws {
        let variants: [(String, String)] = [
            ("cold_wal", "confirmed_transactions_only"),
            ("cold_wal_main_only", "durable_main_state_missing_transactions_unknown"),
            ("cold_wal_empty", "durable_main_state_missing_transactions_unknown"),
            ("cold_wal_header", "durable_main_state_missing_transactions_unknown"),
            ("cold_journal_hot", "journal_recovery_private_copy"),
            ("cold_journal_inactive", "journal_recovery_private_copy"),
            ("cold_journal", "journal_recovery_private_copy")
        ]
        for (mode, semantics) in variants {
            let value: RPCValue = .object(["role": .string("memory"), "mode": .string(mode), "semantics": .string(semantics)])
            let parsed = try MaintenanceSqliteRecovery(value)
            XCTAssertEqual(parsed.mode, mode)
            XCTAssertEqual(parsed.semantics, semantics)
            XCTAssertEqual(parsed.id, "maintenance-recovery:memory")
            let changed: RPCValue = .object(["role": .string("memory"), "mode": .string(mode), "semantics": .string("all_data_recovered")])
            XCTAssertThrowsError(try MaintenanceSqliteRecovery(changed))
        }
        XCTAssertThrowsError(try MaintenanceSqliteRecovery(.object(["role": .string("memory"), "mode": .string("future_auto_repair"), "semantics": .string("confirmed_transactions_only")])))
    }

    func testReleaseReceiptCannotClaimProfileRestoreOrMissingManifest() throws {
        let id = String(repeating: "b", count: 32)
        let fields: [String: RPCValue] = [
            "operation_id": .string(id), "purpose": .string("release.update.apply"),
            "status": .string("completed"), "effect_status": .string("current_selected"),
            "target_name": .string("jarvis-v2"), "artifact_id": .null,
            "manifest_sha256": .string(String(repeating: "a", count: 64)),
            "target_identity": .object(["device": .string("1"), "inode": .string("2")]),
            "new_profile_id": .null, "error_code": .null, "cleanup_error_code": .null,
            "cleanup_pending": .bool(false)
        ]
        XCTAssertEqual(try MaintenanceReceipt(.object(fields), operation: id, kind: "release.update.apply").effect, "current_selected")
        var wrong = fields; wrong["effect_status"] = .string("new_disarmed_profile")
        XCTAssertThrowsError(try MaintenanceReceipt(.object(wrong), operation: id, kind: "release.update.apply"))
        wrong = fields; wrong["manifest_sha256"] = .null
        XCTAssertThrowsError(try MaintenanceReceipt(.object(wrong), operation: id, kind: "release.update.apply"))
    }
}
