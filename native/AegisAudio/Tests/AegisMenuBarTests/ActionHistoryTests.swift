import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class ActionHistoryTests: XCTestCase {
    private func id(_ value: Int) -> UUID {
        UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", value))!
    }

    private func row(_ sequence: Int, conversation: UUID? = nil) -> [String: Any] {
        ["id": id(sequence).uuidString, "sequence": sequence,
         "timestamp": "2026-10-22T12:00:00Z", "tool_name": "filesystem_read_text",
         "state": "reported_success", "conversation_id": conversation?.uuidString as Any? ?? NSNull(),
         "execution_count": 2, "failure_count": 1]
    }

    private func page(_ values: [Int], revision: Int = 900, next: Int? = nil,
                      conversation: UUID? = nil) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "revision": id(revision).uuidString, "entries": values.map { row($0, conversation: conversation) },
            "next_before_sequence": next as Any? ?? NSNull(), "retention_limit": 1000,
            "recording_incomplete": false,
        ])
    }

    private func receipt(_ entry: UUID?, revision: Int = 901, count: Int = 1) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "revision": id(revision).uuidString, "entry_id": entry?.uuidString as Any? ?? NSNull(),
            "deleted": count,
        ])
    }

    func testParserKeepsOnlyExpectedMetadataAndLabelsReportedSuccessAccurately() throws {
        let parsed = try JarvisActionHistoryPage(page([2, 1], conversation: id(500)))
        XCTAssertEqual(parsed.entries.map(\.sequence), [2, 1])
        XCTAssertEqual(parsed.entries[0].conversationID, id(500))
        XCTAssertEqual(parsed.entries[0].executionCount, 2)
        XCTAssertEqual(parsed.entries[0].failureCount, 1)
        XCTAssertEqual(parsed.entries[0].toolTitle, "Lectura de archivo")
        XCTAssertEqual(parsed.entries[0].stateLabel, "La herramienta informó éxito")
        XCTAssertNil(parsed.nextBeforeSequence)
    }

    func testToolTitlesDescribeCapabilityWithoutGuessingAnAction() {
        let computer = JarvisActionEntry(id: id(1), sequence: 1, timestamp: Date(timeIntervalSince1970: 0),
                                        toolName: "computer_use", state: "failed", conversationID: nil)
        XCTAssertEqual(computer.toolTitle, "Control del Mac")
        let unknown = JarvisActionEntry(id: id(2), sequence: 2, timestamp: computer.timestamp,
                                       toolName: "custom_synthetic_tool", state: "reported_success", conversationID: nil)
        XCTAssertNil(unknown.toolTitle)
    }

    func testParserRejectsMalformedRowsOrderingAndCursors() throws {
        let source = try JSONSerialization.jsonObject(with: page([2, 1])) as! [String: Any]
        var cases: [[String: Any]] = []
        for field in ["sequence", "execution_count", "failure_count"] {
            var badRow = row(1); badRow[field] = true
            var bad = source; bad["entries"] = [badRow]; cases.append(bad)
        }
        for rows in [[row(1), row(2)], [row(1), row(1)]] {
            var bad = source; bad["entries"] = rows; cases.append(bad)
        }
        var badTool = row(1); badTool["tool_name"] = "https://private.invalid"
        var bad = source; bad["entries"] = [badTool]; cases.append(bad)
        bad = source; bad["next_before_sequence"] = 99; cases.append(bad)
        bad = source; bad["recording_incomplete"] = 1; cases.append(bad)
        var badCounts = row(1); badCounts["failure_count"] = 3
        bad = source; bad["entries"] = [badCounts]; cases.append(bad)
        for value in cases {
            XCTAssertThrowsError(try JarvisActionHistoryPage(JSONSerialization.data(withJSONObject: value)))
        }
    }

    func testPaginationBindsRevisionFilterAndRejectsOverlappingRows() async throws {
        let store = JarvisActionHistoryStore()
        let conversation = id(500)
        await store.load(conversationID: conversation) { payload in
            XCTAssertEqual(payload["conversation_id"] as? String, conversation.uuidString.lowercased())
            return try self.page([5, 4], next: 4, conversation: conversation)
        }
        await store.load(conversationID: conversation, append: true) { payload in
            XCTAssertEqual(payload["before_sequence"] as? Int, 4)
            XCTAssertEqual(payload["revision"] as? String, self.id(900).uuidString.lowercased())
            return try self.page([3, 2], next: 2, conversation: conversation)
        }
        XCTAssertEqual(store.entries.map(\.sequence), [5, 4, 3, 2])
        await store.load(conversationID: conversation, append: true) { _ in
            try self.page([3, 1], conversation: conversation)
        }
        XCTAssertNotNil(store.error)
        XCTAssertTrue(store.requiresRefresh)
        XCTAssertEqual(store.entries.map(\.sequence), [5, 4, 3, 2])
    }

    func testErrorPreservesRowsAndDoesNotMasqueradeAsEmptyHistory() async throws {
        let store = JarvisActionHistoryStore()
        await store.load { _ in try self.page([1]) }
        await store.load { _ in throw JarvisActionHistoryError.unavailable }
        XCTAssertEqual(store.entries.map(\.sequence), [1])
        XCTAssertNotNil(store.error)
        XCTAssertTrue(store.requiresRefresh)
        XCTAssertFalse(store.loading)
        await store.load { _ in try self.page([]) }
        XCTAssertTrue(store.entries.isEmpty)
        XCTAssertTrue(store.loaded)
        XCTAssertNil(store.error)
    }

    func testLateListCannotResurrectConfirmedDeletedRows() async throws {
        let store = JarvisActionHistoryStore()
        await store.load { _ in try self.page([1]) }
        var pending: CheckedContinuation<Data, Never>?
        let read = Task {
            await store.load { _ in await withCheckedContinuation { pending = $0 } }
        }
        while pending == nil { await Task.yield() }
        await store.delete(entryID: nil, expectedRevision: id(900)) { revision, entry in
            XCTAssertEqual(revision, self.id(900)); XCTAssertNil(entry)
            return try self.receipt(nil)
        }
        pending?.resume(returning: try page([1]))
        await read.value
        XCTAssertTrue(store.entries.isEmpty)
        XCTAssertEqual(store.revision, id(901))
        XCTAssertTrue(store.requiresRefresh)
    }

    func testDeletionWaitsForValidReceiptAndPreservesRowsOnUnknownOutcome() async throws {
        let store = JarvisActionHistoryStore()
        await store.load { _ in try self.page([2, 1]) }
        var pending: CheckedContinuation<Data, Never>?
        let deletion = Task {
            await store.delete(entryID: self.id(1), expectedRevision: self.id(900)) { _, _ in
                await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        XCTAssertEqual(store.entries.map(\.sequence), [2, 1])
        XCTAssertTrue(store.deleting)
        pending?.resume(returning: try receipt(id(2))) // A receipt for another target is not proof.
        await deletion.value
        XCTAssertEqual(store.entries.map(\.sequence), [2, 1])
        XCTAssertEqual(store.error, JarvisActionHistoryError.deletionUnconfirmed.message)
        XCTAssertTrue(store.requiresRefresh)
        var retried = false
        await store.delete(entryID: id(1), expectedRevision: id(900)) { _, _ in
            retried = true; return try self.receipt(self.id(1))
        }
        XCTAssertFalse(retried)
    }

    func testSingleDeletionKeepsOtherRowsAndRequiresFreshPagination() async throws {
        let store = JarvisActionHistoryStore()
        await store.load { _ in try self.page([3, 2], next: 2) }
        await store.delete(entryID: id(2), expectedRevision: id(900)) { _, _ in
            try self.receipt(self.id(2))
        }
        XCTAssertEqual(store.entries.map(\.sequence), [3])
        XCTAssertNil(store.nextBeforeSequence)
        XCTAssertTrue(store.requiresRefresh)
        XCTAssertNil(store.error)
    }

    func testChangedSnapshotRequiresNewOwnerConfirmationWithoutSendingDelete() async throws {
        let store = JarvisActionHistoryStore()
        await store.load { _ in try self.page([1]) }
        let approvedRevision = try XCTUnwrap(store.revision)
        await store.load { _ in try self.page([2, 1], revision: 902) }
        var sent = false
        await store.delete(entryID: nil, expectedRevision: approvedRevision) { _, _ in
            sent = true; return try self.receipt(nil)
        }
        XCTAssertFalse(sent)
        XCTAssertEqual(store.entries.map(\.sequence), [2, 1])
        XCTAssertEqual(store.error, JarvisActionHistoryError.changed.message)
    }

    func testFilterChangeInvalidatesEarlierResponseAndItsError() async throws {
        let store = JarvisActionHistoryStore()
        var pending: CheckedContinuation<Data, Never>?
        let old = Task { await store.load { _ in await withCheckedContinuation { pending = $0 } } }
        while pending == nil { await Task.yield() }
        let conversation = id(500)
        await store.load(conversationID: conversation) { _ in
            try self.page([2], revision: 901, conversation: conversation)
        }
        pending?.resume(returning: Data("invalid".utf8))
        await old.value
        XCTAssertEqual(store.entries.map(\.sequence), [2])
        XCTAssertEqual(store.conversationID, conversation)
        XCTAssertNil(store.error)
    }
}
