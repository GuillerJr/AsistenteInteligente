import Foundation
import XCTest
@testable import DesktopCore

final class MaintenanceProductCompatibilityTests: XCTestCase {
    func testProductRequiresCompleteCurrentInventoryAndPreservesHistoricalInventories() throws {
        for roles in [MaintenanceValidation.legacyRoles, MaintenanceValidation.pluginRoles,
                      MaintenanceValidation.automationRoles, MaintenanceValidation.roles] {
            XCTAssertTrue(MaintenanceValidation.acceptsRoles(roles, includesConfiguration: true))
            XCTAssertTrue(MaintenanceValidation.acceptsRoles(roles.subtracting(["configuration_portable"]),
                includesConfiguration: false))
        }
        XCTAssertFalse(MaintenanceValidation.acceptsRoles(MaintenanceValidation.pluginRoles.union(["product"]),
            includesConfiguration: true))
        XCTAssertFalse(MaintenanceValidation.acceptsRoles(MaintenanceValidation.roles.union(["unknown_resource"]),
            includesConfiguration: true))
        var value = MaintenanceFixtures.review.object!
        var metadata = value["metadata"]!.object!
        metadata["source"] = .array(MaintenanceValidation.roles.subtracting(["configuration_portable"]).sorted().map {
            .object(["role": .string($0), "disposition": .string("present"), "bytes": .number(128)])
        })
        value["metadata"] = .object(metadata)
        value["review_digest"] = .string(ManagementValidation.digest(try MaintenanceValidation.canonical(.object(metadata))))
        let review = try MaintenanceReview(.object(value))
        XCTAssertEqual(Set(review.sources.map(\.role)), MaintenanceValidation.roles.subtracting(["configuration_portable"]))
        XCTAssertEqual(MaintenancePresentation.role("product"), "Producto y coordinación conservados")
    }

    func testDoctorAcceptsConservedProductCountsButRejectsUnknownTrustedRole() throws {
        let counts = ["product_archives", "product_snapshot_blobs", "product_project_history",
                      "product_interaction_history", "product_session_history"]
        var row: [String: RPCValue] = ["role": .string("product"), "status": .string("ok"),
            "code": .null, "identity": .null, "permissions": .null, "byte_length": .number(128),
            "format_version": .number(1), "application_id": .null, "schema_version": .null,
            "integrity": .string("ok"), "counts": .array(counts.map { .array([.string($0), .number(0)]) }),
            "inspection_source": .string("host_ram_snapshot"), "snapshot_byte_length": .number(128),
            "snapshot_sha256": .string(String(repeating: "f", count: 64))]
        let check = try DoctorCheckSummary(.object(row))
        XCTAssertEqual(check.role, "product"); XCTAssertEqual(check.source, "host_ram_snapshot")
        row["role"] = .string("unknown_resource")
        XCTAssertThrowsError(try DoctorCheckSummary(.object(row)))
    }
}
