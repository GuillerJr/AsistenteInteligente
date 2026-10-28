import Foundation
import XCTest
@testable import DesktopCore

final class MaintenancePluginCompatibilityTests: XCTestCase {
    private func review(_ transform: ([RPCValue]) -> [RPCValue]) throws -> RPCValue {
        var value = MaintenanceFixtures.review.object!
        var metadata = value["metadata"]!.object!
        metadata["source"] = .array(transform(metadata["source"]!.array!))
        value["metadata"] = .object(metadata)
        value["review_digest"] = .string(ManagementValidation.digest(try MaintenanceValidation.canonical(.object(metadata))))
        return .object(value)
    }
    private var plugin: RPCValue { .object(["role": .string("plugins"), "disposition": .string("owned"), "bytes": .number(12)]) }
    func testHistoricalSixAndCurrentSevenSourceReviewsRemainCompleteAndDigestBound() throws {
        let legacy = try MaintenanceReview(MaintenanceFixtures.review)
        XCTAssertEqual(legacy.sources.count, 6)
        let current = try MaintenanceReview(review { $0 + [plugin] })
        XCTAssertEqual(current.sources.count, 7)
        XCTAssertTrue(current.sources.contains { $0.role == "plugins" })
        XCTAssertEqual(MaintenancePresentation.role("plugins"), "Plugins")
    }
    func testPluginRoleDoesNotPermitDuplicatesOrIncompleteHistoricalSources() throws {
        XCTAssertThrowsError(try MaintenanceReview(review { $0 + [plugin, plugin] }))
        XCTAssertThrowsError(try MaintenanceReview(review { $0.filter { $0["role"].string != "state" } + [plugin] }))
        XCTAssertThrowsError(try MaintenanceReview(review { Array($0.dropLast()) }))
    }
    private func activation(_ transform: ([RPCValue]) -> [RPCValue]) throws -> RPCValue {
        var value = try RestoreFixture.reviewValue().object!
        var metadata = value["metadata"]!.object!
        metadata["resources"] = .array(transform(metadata["resources"]!.array!))
        value["metadata"] = .object(metadata)
        value["review_digest"] = .string(ManagementValidation.digest(try MaintenanceValidation.canonical(.object(metadata))))
        return .object(value)
    }
    private var restoredPlugin: RPCValue {
        .object(["role": .string("plugins"), "present": .bool(false), "sha256": .null,
                 "byte_length": .number(0), "counts": .array([])])
    }
    func testActivationAcceptsExactHistoricalSevenAndCurrentEightResourceProofs() throws {
        let historical = try RestoreProfileReview(RestoreFixture.reviewValue())
        XCTAssertEqual(historical.resources.count, 7)
        let current = try RestoreProfileReview(activation { $0 + [restoredPlugin] })
        XCTAssertEqual(current.resources.count, 8)
        XCTAssertEqual(Set(current.resources.map(\.role)), MaintenanceValidation.roles)
        XCTAssertFalse(current.resources.first(where: { $0.role == "plugins" })!.present)
    }
    func testActivationRejectsDuplicateIncompleteAndDigestChangedPluginInventories() throws {
        XCTAssertThrowsError(try RestoreProfileReview(activation { $0 + [restoredPlugin, restoredPlugin] }))
        XCTAssertThrowsError(try RestoreProfileReview(activation { $0.filter { $0["role"].string != "skills" } + [restoredPlugin] }))
        XCTAssertThrowsError(try RestoreProfileReview(activation { $0 + [$0[0]] }))
        var changed = try activation { $0 + [restoredPlugin] }.object!
        changed["review_digest"] = try RestoreFixture.reviewValue()["review_digest"]
        XCTAssertThrowsError(try RestoreProfileReview(.object(changed)), "An accepted role inventory still requires its exact digest")
    }
}
