import Foundation
import XCTest
@testable import DesktopCore

final class AutomationRecoveryContractTests: XCTestCase {
    private let originalRoles = ["state", "memory", "schedules", "agents", "skills", "telegram", "configuration_portable"]
    private let hash = String(repeating: "f", count: 64)

    private func wrap(_ metadata: RPCValue) throws -> RPCValue {
        .object([
            "review_id": .string(String(repeating: "a", count: 32)),
            "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata))),
            "expires_in_seconds": .number(120), "metadata": metadata,
        ])
    }

    private func source(_ role: String) -> RPCValue {
        .object(["role": .string(role), "disposition": .string("present"), "bytes": .number(2048)])
    }

    private func review(roles: [String], restoring: Bool = false, disarms: [String]? = nil) throws -> RPCValue {
        var metadata = try XCTUnwrap(MaintenanceFixtures.review["metadata"].object)
        metadata["source"] = .array(roles.map(source))
        if restoring {
            metadata["kind"] = .string("restore.stage")
            metadata["manifest_sha256"] = .string(hash)
            metadata["artifact_id"] = .string(String(repeating: "b", count: 32))
            metadata["source_artifact_identity"] = metadata["destination_identity"]
            metadata["disarms"] = .array((disarms ?? MaintenanceValidation.disarms.sorted()).map(RPCValue.string))
        }
        return try wrap(.object(metadata))
    }

    private func activation(roles: [String]) throws -> RPCValue {
        var metadata = try XCTUnwrap(RestoreFixture.reviewValue()["metadata"].object)
        metadata["resources"] = .array(roles.map { role in
            .object(["role": .string(role), "present": .bool(false), "sha256": .null,
                     "byte_length": .number(0), "counts": .array([])])
        })
        return try wrap(.object(metadata))
    }

    func testBackupReviewAcceptsExactLegacyPluginAndAutomationInventories() throws {
        let baseline = originalRoles.filter { $0 != "configuration_portable" }
        for roles in [baseline, baseline + ["plugins"], baseline + ["plugins", "automations"]] {
            let parsed = try MaintenanceReview(review(roles: roles))
            XCTAssertEqual(parsed.sources.count, roles.count)
            XCTAssertEqual(Set(parsed.sources.map(\.role)), Set(roles))
        }
        XCTAssertEqual(MaintenancePresentation.role("automations"), "Automatizaciones y flujos")
    }

    func testInventoriesRejectMissingRequiredRoleDuplicateAndUnknownRoleDespiteValidDigest() throws {
        let baseline = originalRoles.filter { $0 != "configuration_portable" }
        let current = baseline + ["plugins", "automations"]
        let invalid = [
            baseline + ["automations"], current.filter { $0 != "memory" },
            current + ["agents"], Array(current.dropLast()) + ["unknown_resource"],
        ]
        for roles in invalid {
            XCTAssertThrowsError(try MaintenanceReview(review(roles: roles)))
            XCTAssertThrowsError(try RestoreProfileReview(activation(roles: roles + ["configuration_portable"])))
        }
    }

    func testRestoreReviewRequiresAutomationDisarmAndAcceptsExactHistoricalPolicies() throws {
        let baseline = originalRoles.filter { $0 != "configuration_portable" }
        let historical = MaintenanceValidation.legacyDisarms.sorted()
        for roles in [baseline, baseline + ["plugins"]] {
            XCTAssertNoThrow(try MaintenanceReview(review(roles: roles, restoring: true, disarms: historical)))
            // A new host can restore an old archive using today's stronger policy.
            XCTAssertNoThrow(try MaintenanceReview(review(roles: roles, restoring: true)))
        }
        let current = baseline + ["plugins", "automations"]
        XCTAssertNoThrow(try MaintenanceReview(review(roles: current, restoring: true)))
        XCTAssertThrowsError(try MaintenanceReview(review(roles: current, restoring: true, disarms: historical)))
        for invalid in [historical + ["unknown_disarm"], historical + [historical[0]], Array(historical.dropLast())] {
            XCTAssertThrowsError(try MaintenanceReview(review(roles: baseline, restoring: true, disarms: invalid)))
        }
    }

    func testActivationReviewAcceptsSevenEightAndNineExactResourcesWithoutStartingAnything() throws {
        for roles in [originalRoles, originalRoles + ["plugins"], originalRoles + ["plugins", "automations"]] {
            let parsed = try RestoreProfileReview(activation(roles: roles))
            XCTAssertEqual(Set(parsed.resources.map(\.role)), Set(roles))
            XCTAssertTrue(parsed.resources.allSatisfy { !$0.present })
        }
    }

    private func check(_ role: String) -> RPCValue {
        let sqlite = role == "schedules"
        return .object([
            "role": .string(role), "status": .string("ok"), "code": .null,
            "identity": .null, "permissions": .null, "byte_length": .number(4096),
            "format_version": sqlite ? .null : .number(1),
            "application_id": sqlite ? .number(0x4A525343) : .null,
            "schema_version": sqlite ? .number(2) : .null,
            "integrity": .string(sqlite ? "ok" : "not_checked"),
            "counts": .array([.array([.string(sqlite ? "wake_events" : "automation_flows"), .number(0)])]),
            "inspection_source": .string(sqlite ? "host_ram_snapshot" : "metadata"),
            "snapshot_byte_length": sqlite ? .number(4096) : .null,
            "snapshot_sha256": sqlite ? .string(hash) : .null,
        ])
    }

    private func doctor(roles: [String]) -> RPCValue {
        .object(["report": .object([
            "report_id": .string(String(repeating: "b", count: 32)),
            "profile_id": .string("synthetic_profile"), "generation": .number(7),
            "status": .string("ok"), "coherence": .string("offline_quiescent"),
            "profile_identity": .null, "checks": .array(roles.map(check)),
            "blockers": .array([]), "suggestions": .array([]),
            "supported_repair_actions": .array([]), "effect": .string("read_only_diagnostic"),
        ])])
    }

    func testDoctorAcceptsAutomationRoleAndSchedulerV2WithoutDroppingOtherChecks() throws {
        let roles = originalRoles + ["plugins", "automations", "restore_record", "cross_references"]
        let report = try DoctorSummary(doctor(roles: roles))
        XCTAssertEqual(report.checks.count, 11)
        XCTAssertTrue(report.checks.contains { $0.role == "automations" })
        XCTAssertEqual(report.checks.first { $0.role == "schedules" }?.source, "host_ram_snapshot")
        XCTAssertThrowsError(try DoctorSummary(doctor(roles: roles + ["automations"])))
        XCTAssertThrowsError(try DoctorSummary(doctor(roles: Array(roles.dropLast()) + ["unknown_resource"])))
    }

    func testVerificationAcceptsNineResourceHashesAndStillRejectsUnknownOrDuplicate() throws {
        let roles = originalRoles + ["plugins", "automations"]
        func verification(_ roles: [String]) -> RPCValue {
            .object([
                "verification_id": .string(String(repeating: "c", count: 32)),
                "verification": .object([
                    "artifact_id": .string(String(repeating: "d", count: 32)),
                    "profile_id": .string("synthetic_profile"), "generation": .number(7),
                    "manifest_sha256": .string(hash), "root_identity": RestoreFixture.identity,
                    "resource_hashes": .array(roles.map { .array([.string($0), .string(hash)]) }),
                    "excluded": .array(MaintenanceValidation.exclusions.sorted().map(RPCValue.string)),
                    "authenticity": .string("not_established"),
                ]),
            ])
        }
        XCTAssertNoThrow(try MaintenanceVerification(verification(roles)))
        XCTAssertThrowsError(try MaintenanceVerification(verification(roles + ["automations"])))
        XCTAssertThrowsError(try MaintenanceVerification(verification(Array(roles.dropLast()) + ["unknown_resource"])))
    }
}
