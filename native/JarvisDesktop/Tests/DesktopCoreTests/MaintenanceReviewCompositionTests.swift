import Foundation
import XCTest
@testable import DesktopCore

final class MaintenanceReviewCompositionTests: XCTestCase {
    private func walReview(roles: [String] = ["memory"]) throws -> MaintenanceReview {
        var metadata = MaintenanceFixtures.review["metadata"].object!
        var sources = metadata["source"]!.array!
        for index in sources.indices where roles.contains(sources[index]["role"].string ?? "") {
            var source = sources[index].object!; source["disposition"] = .string("present")
            sources[index] = .object(source)
        }
        metadata["source"] = .array(sources)
        metadata["sqlite_recovery"] = .array(roles.map { .object([
            "role": .string($0), "mode": .string("cold_wal"),
            "semantics": .string("confirmed_transactions_only")]) })
        var row = MaintenanceFixtures.review.object!
        row["metadata"] = .object(metadata)
        row["review_digest"] = .string(ManagementValidation.digest(try MaintenanceValidation.canonical(.object(metadata))))
        return try MaintenanceReview(.object(row))
    }

    func testSiblingSourceAndWalNoticeHaveDisjointStableIdentity() throws {
        let review = try walReview()
        let sourceIDs = Set(review.sources.map(\.id))
        XCTAssertTrue(sourceIDs.isDisjoint(with: review.sqliteRecovery.map(\.id)))
        XCTAssertEqual(Set(review.sqliteRecovery.map(\.id)).count, review.sqliteRecovery.count)
    }

    func testAllThreeWalNoticesRemainDistinctAndStableWhenRecoveryOrderChanges() throws {
        let forward = try walReview(roles: ["state", "memory", "schedules"])
        let reversed = try walReview(roles: ["schedules", "memory", "state"])
        // These are the actual two collections consumed by the shared native
        // row component. Every source and warning must keep its own row.
        let ids = forward.sources.map(\.id) + forward.sqliteRecovery.map(\.id)
        XCTAssertEqual(ids.count, 9)
        XCTAssertEqual(Set(ids).count, ids.count)
        XCTAssertEqual(Set(forward.sqliteRecovery.map(\.id)), Set(reversed.sqliteRecovery.map(\.id)))
    }
}
