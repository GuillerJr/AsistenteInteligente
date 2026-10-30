import Foundation
import XCTest
@testable import DesktopCore

final class SessionRunTrackerTests: XCTestCase {
    @MainActor func testTwoSessionsKeepIndependentRunsAndSelectionRejectsLateResponse() throws {
        let tracker = SessionRunTracker()
        let first = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        let second = SessionScope(sessionID: String(repeating: "b", count: 32), temporary: false)
        let one = String(repeating: "c", count: 32), two = String(repeating: "d", count: 32)
        tracker.record(scope: first, runID: one, phase: "running")
        let original = tracker.select(first)
        try tracker.append(scope: first, runID: one, sequence: 1, delta: "Primero")
        tracker.record(scope: second, runID: two, phase: "waiting_approval")
        _ = tracker.select(second)
        XCTAssertFalse(tracker.accepts(scope: first, runID: one, generation: original))
        XCTAssertEqual(tracker.active.count, 2)
        XCTAssertEqual(tracker.observations[first]?.phase, "running")
        XCTAssertEqual(tracker.observations[second]?.phase, "waiting_approval")
        let returned = tracker.select(first)
        XCTAssertTrue(tracker.accepts(scope: first, runID: one, generation: returned))
        try tracker.append(scope: first, runID: one, sequence: 1, delta: "Duplicado")
        try tracker.append(scope: first, runID: one, sequence: 2, delta: " y siguiente")
        XCTAssertEqual(tracker.observations[first]?.streamingText, "Primero y siguiente")
        XCTAssertEqual(tracker.observations[first]?.lastSequence, 2)
    }

    @MainActor func testSettlementCannotBeReopenedByOldPollAndRecoveryDoesNotKeepTemporaryRuns() {
        let tracker = SessionRunTracker()
        let durable = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        let temporary = SessionScope(sessionID: durable.sessionID, temporary: true)
        let run = String(repeating: "b", count: 32)
        tracker.record(scope: durable, runID: run, phase: "running")
        tracker.record(scope: temporary, runID: run, phase: "running")
        tracker.record(scope: durable, runID: run, phase: "completed")
        tracker.record(scope: durable, runID: run, phase: "running")
        XCTAssertEqual(tracker.observations[durable]?.phase, "completed")
        XCTAssertEqual(tracker.active.count, 1)
        tracker.reset(retainingDurable: true)
        XCTAssertNil(tracker.observations[temporary])
        XCTAssertEqual(tracker.observations[durable]?.phase, "completed")
        XCTAssertNil(tracker.selectedScope)
    }
}
