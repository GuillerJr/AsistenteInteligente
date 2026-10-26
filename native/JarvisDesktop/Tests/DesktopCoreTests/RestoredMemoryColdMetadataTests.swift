import Foundation
import XCTest
@testable import DesktopCore

final class RestoredMemoryColdMetadataTests: XCTestCase {
    func testWALAndJournalReviewsExposePhysicalMutationWarningAndMatchOnlySameInventory() throws {
        for kind in ["cold_wal_snapshot", "cold_journal_snapshot"] {
            let inventory = try RestoredMemoryInventory(RestoredMemoryFixture.inventory(["source_kind": .string(kind)]))
            let review = try RestoredMemoryReview(RestoredMemoryFixture.review(["source_kind": .string(kind), "cold_sqlite_recovery_required": .bool(true)]))
            XCTAssertTrue(review.coldSQLiteRecoveryRequired)
            XCTAssertTrue(review.matches(inventory, source: try XCTUnwrap(inventory.sources.first)))
            let other = try RestoredMemoryInventory(RestoredMemoryFixture.inventory())
            XCTAssertFalse(review.matches(other, source: try XCTUnwrap(other.sources.first)))
        }
    }
    func testSourceKindCannotHideWALMutationOrClaimUnknownRecoveryMode() throws {
        for kind in ["cold_wal_snapshot", "cold_journal_snapshot"] {
            XCTAssertThrowsError(try RestoredMemoryReview(RestoredMemoryFixture.review(["source_kind": .string(kind)])))
        }
        XCTAssertThrowsError(try RestoredMemoryReview(RestoredMemoryFixture.review(["cold_sqlite_recovery_required": .bool(true)])))
        XCTAssertThrowsError(try RestoredMemoryReview(RestoredMemoryFixture.review(["source_kind": .string("repaired_unverified")])))
        XCTAssertThrowsError(try RestoredMemoryInventory(RestoredMemoryFixture.inventory(["source_kind": .string("repaired_unverified")])))
    }
}
