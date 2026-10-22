import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class ConversationSearchTests: XCTestCase {
    private func cursor(_ value: Int) -> String { "page_\(value)." + String(repeating: "a", count: 64) }
    private func id(_ value: Int) -> UUID {
        UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", value))!
    }
    private func row(_ value: Int, archived: Bool = false, filed: Bool = false) -> [String: Any] {
        ["conversation_id": id(value).uuidString.lowercased(), "title": "Título \(value)",
         "updated_at": "2026-10-22T12:00:00Z", "archived": archived, "filed": filed,
         "search_match": NSNull()]
    }
    private func page(_ values: [Int] = [], next: String? = nil,
                      archived: Bool = false, filed: Bool = false) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "conversations": values.map { row($0, archived: archived, filed: filed) },
            "next_cursor": next as Any? ?? NSNull(),
        ])
    }

    private func matchedHistory() throws -> (ChatConversationSummary, ChatHistoryPage) {
        let match = ChatSearchMatch(turnID: id(10), sequence: 69, excerpt: "Mensaje antiguo")
        let entry = ChatConversationSummary(id: id(1), title: "Antiguo", updatedAt: "today", archived: false,
                                            searchMatch: match)
        let page = try ChatHistoryPage(JSONSerialization.data(withJSONObject: [
            "conversation_id": entry.id.uuidString.lowercased(),
            "turns": [["conversation_id": entry.id.uuidString.lowercased(),
                       "turn_id": match.turnID.uuidString.lowercased(), "sequence": match.sequence,
                       "content": "Mensaje antiguo", "role": "user"]],
            "next_before_sequence": match.sequence,
        ]))
        return (entry, page)
    }

    func testSearchContinuesEmptyPagesAndPreservesMatchingMetadata() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = "  aguja  "
        var matched = row(1)
        matched["search_match"] = ["turn_id": id(10).uuidString.lowercased(), "sequence": 69,
                                    "excerpt": "Una aguja antigua"]
        let final = try JSONSerialization.data(withJSONObject: [
            "conversations": [matched], "next_cursor": NSNull(),
        ])
        let empty = try page(next: cursor(1))
        var calls = 0
        await chat.refreshHistory { method, payload in
            XCTAssertEqual(method, "conversations.search")
            XCTAssertEqual(payload["query"] as? String, "aguja")
            XCTAssertNil(payload["offset"])
            calls += 1
            if calls == 1 { XCTAssertNil(payload["cursor"]); return empty }
            XCTAssertEqual(payload["cursor"] as? String, self.cursor(1))
            return final
        }
        XCTAssertEqual(calls, 2)
        XCTAssertEqual(chat.conversations.first?.searchMatch?.sequence, 69)
        XCTAssertEqual(chat.conversations.first?.searchMatch?.turnID, id(10))
        XCTAssertFalse(chat.historySearchIncomplete)
        XCTAssertFalse(chat.historyLoading)
        XCTAssertNil(chat.historyError)
    }

    func testQueryRoundTripInvalidatesPendingTextAndErrorsBeforeDebounce() async throws {
        for response in [try page([1]), nil] {
            let chat = JarvisChatStore()
            chat.historyQuery = "A"
            var pending: CheckedContinuation<Data?, Never>?
            let task = Task {
                await chat.refreshHistory { _, _ in
                    await withCheckedContinuation { pending = $0 }
                }
            }
            while pending == nil { await Task.yield() }
            chat.historyQuery = "B"
            chat.historyQuery = "A"
            XCTAssertFalse(chat.historyLoading)
            XCTAssertTrue(chat.conversations.isEmpty)
            pending?.resume(returning: response)
            await task.value
            XCTAssertTrue(chat.conversations.isEmpty)
            XCTAssertNil(chat.historyError)
            XCTAssertNil(chat.error)
        }
    }

    func testFilterChangesDiscardOldResultsAndProtectNewLoadingState() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = "aguja"
        var oldResponse: CheckedContinuation<Data?, Never>?
        var newResponse: CheckedContinuation<Data?, Never>?
        let oldTask = Task {
            await chat.refreshHistory { _, _ in
                await withCheckedContinuation { oldResponse = $0 }
            }
        }
        while oldResponse == nil { await Task.yield() }
        chat.showArchived = true
        let newTask = Task {
            await chat.refreshHistory { _, payload in
                XCTAssertEqual(payload["filed"] as? Bool, true)
                return await withCheckedContinuation { newResponse = $0 }
            }
        }
        while newResponse == nil { await Task.yield() }
        oldResponse?.resume(returning: try page([1]))
        await oldTask.value
        XCTAssertTrue(chat.historyLoading)
        XCTAssertTrue(chat.conversations.isEmpty)
        newResponse?.resume(returning: try page([2], filed: true))
        await newTask.value
        XCTAssertEqual(chat.conversations.map(\.id), [id(2)])
        chat.showTrash = true
        XCTAssertTrue(chat.conversations.isEmpty)
        XCTAssertNil(chat.nextSearchCursor)
    }

    func testLargeSearchHasExplicitContinuationAndDoesNotRestartAtFirstPage() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = "lejana"
        var calls = 0
        await chat.refreshHistory { _, payload in
            calls += 1
            if calls > 1 { XCTAssertEqual(payload["cursor"] as? String, self.cursor(calls - 1)) }
            return try! self.page(next: self.cursor(calls))
        }
        XCTAssertEqual(calls, 32)
        XCTAssertTrue(chat.historySearchIncomplete)
        XCTAssertTrue(chat.conversations.isEmpty)
        XCTAssertFalse(chat.historyLoading)
        await chat.refreshHistory(append: true) { _, payload in
            XCTAssertEqual(payload["cursor"] as? String, self.cursor(32))
            return try! self.page([7])
        }
        XCTAssertEqual(chat.conversations.map(\.id), [id(7)])
        XCTAssertFalse(chat.historySearchIncomplete)
    }

    func testPauseDiscardsLatePageAndResumesAtLastAcceptedCursor() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = "aguja"
        var pending: CheckedContinuation<Data?, Never>?
        var calls = 0
        let task = Task {
            await chat.refreshHistory { _, _ in
                calls += 1
                if calls == 1 { return try! self.page([1], next: self.cursor(1)) }
                return await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        chat.pauseHistorySearch()
        XCTAssertFalse(chat.historyLoading)
        XCTAssertTrue(chat.historySearchIncomplete)
        pending?.resume(returning: try page([2], next: cursor(2)))
        await task.value
        XCTAssertEqual(chat.conversations.map(\.id), [id(1)])
        XCTAssertEqual(chat.nextSearchCursor, cursor(1))
        await chat.refreshHistory(append: true) { _, payload in
            XCTAssertEqual(payload["cursor"] as? String, self.cursor(1))
            return try! self.page([1, 2])
        }
        XCTAssertEqual(chat.conversations.map(\.id), [id(1), id(2)])
        XCTAssertFalse(chat.historySearchIncomplete)
    }

    func testCursorCycleRejectsPageBeforeMutatingVisibleRows() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = "aguja"
        var calls = 0
        await chat.refreshHistory { _, _ in
            calls += 1
            return try! self.page([calls], next: self.cursor(calls == 3 ? 1 : calls))
        }
        XCTAssertEqual(calls, 3)
        XCTAssertEqual(chat.conversations.map(\.id), [id(1), id(2)])
        XCTAssertEqual(chat.nextSearchCursor, cursor(2))
        XCTAssertNotNil(chat.historyError)
        XCTAssertFalse(chat.historyLoading)
    }

    func testSearchPageRejectsMalformedMatchesCursorsDuplicatesAndWrongFilters() throws {
        var matched = row(1)
        for match: [String: Any] in [
            ["turn_id": id(10).uuidString, "sequence": true, "excerpt": "a"],
            ["turn_id": id(10).uuidString, "sequence": 0, "excerpt": "a"],
            ["turn_id": id(10).uuidString, "sequence": 1.5, "excerpt": "a"],
            ["turn_id": "wrong", "sequence": 1, "excerpt": "a"],
            ["turn_id": id(10).uuidString, "sequence": 1, "excerpt": String(repeating: "x", count: 195)],
        ] {
            matched["search_match"] = match
            let payload = try JSONSerialization.data(withJSONObject: [
                "conversations": [matched], "next_cursor": NSNull(),
            ])
            XCTAssertThrowsError(try ChatSearchPage(payload, archived: false, filed: false))
        }
        XCTAssertThrowsError(try ChatSearchPage(page([1, 1]), archived: false, filed: false))
        XCTAssertThrowsError(try ChatSearchPage(page([1], archived: true), archived: false, filed: false))
        XCTAssertThrowsError(try ChatSearchPage(page([1], filed: true), archived: false, filed: false))
        for cursor in ["", "wrong", "a." + String(repeating: "g", count: 64)] {
            XCTAssertThrowsError(try ChatSearchPage(page(next: cursor), archived: false, filed: false))
        }
        // A valid partial search may contain no hits yet.
        XCTAssertNotNil(try ChatSearchPage(page(next: cursor(1)), archived: false, filed: false).nextCursor)
    }

    func testSearchPageRequiresExplicitBooleanFiltersAndMatchField() throws {
        for field in ["archived", "filed", "search_match"] {
            var missing = row(1)
            missing.removeValue(forKey: field)
            let data = try JSONSerialization.data(withJSONObject: [
                "conversations": [missing], "next_cursor": NSNull(),
            ])
            XCTAssertThrowsError(try ChatSearchPage(data, archived: false, filed: false))
        }
        for field in ["archived", "filed"] {
            for invalid: Any in [0, 1, "false", NSNull()] {
                var malformed = row(1)
                malformed[field] = invalid
                let data = try JSONSerialization.data(withJSONObject: [
                    "conversations": [malformed], "next_cursor": NSNull(),
                ])
                XCTAssertThrowsError(try ChatSearchPage(data, archived: false, filed: false))
            }
        }
        XCTAssertThrowsError(try ChatSearchPage(page(Array(1...51)), archived: false, filed: false))
        XCTAssertEqual(try ChatSearchPage(page([1]), archived: false, filed: false).conversations.count, 1)
    }

    func testSearchMatchRejectsNonFiniteOrNonIntegerSequence() throws {
        for invalid in [Double.nan, Double.infinity, -Double.infinity, 1.5] {
            XCTAssertThrowsError(try ChatSearchMatch([
                "turn_id": id(10).uuidString, "sequence": invalid, "excerpt": "Aguja",
            ]))
        }
        XCTAssertEqual(try ChatSearchMatch([
            "turn_id": id(10).uuidString, "sequence": 69, "excerpt": "Aguja",
        ]).sequence, 69)
    }

    func testOpeningSearchResultRequiresTheMatchedTurnIdentityAndSequence() throws {
        let chat = JarvisChatStore()
        let matchedTurn = id(10)
        let match = ChatSearchMatch(turnID: matchedTurn, sequence: 69, excerpt: "Mensaje antiguo")
        let entry = ChatConversationSummary(id: id(1), title: "Antiguo", updatedAt: "today", archived: false,
                                            searchMatch: match)
        func history(turnID: UUID, sequence: Int) throws -> ChatHistoryPage {
            try ChatHistoryPage(JSONSerialization.data(withJSONObject: [
                "conversation_id": entry.id.uuidString.lowercased(),
                "turns": [["conversation_id": entry.id.uuidString.lowercased(),
                           "turn_id": turnID.uuidString.lowercased(), "sequence": sequence,
                           "content": "Mensaje antiguo", "role": "user"]],
                "next_before_sequence": sequence,
            ]))
        }
        XCTAssertThrowsError(try chat.restoreConversation(entry, page: history(turnID: id(11), sequence: 69)))
        XCTAssertThrowsError(try chat.restoreConversation(entry, page: history(turnID: matchedTurn, sequence: 70)))
        XCTAssertNil(chat.conversationID)
        try chat.restoreConversation(entry, page: history(turnID: matchedTurn, sequence: 69))
        XCTAssertEqual(chat.conversationID, entry.id)
        XCTAssertEqual(chat.transcript.messages.last?.id, matchedTurn)
        XCTAssertEqual(chat.nextBeforeSequence, 69)
        XCTAssertTrue(chat.transcript.hasNewerHistory)
        XCTAssertFalse(chat.transcript.begin("No enviar desde una ventana antigua"))
        chat.transcript.restore([])
        XCTAssertFalse(chat.transcript.hasNewerHistory)
        XCTAssertTrue(chat.transcript.begin("Enviar desde recientes"))
    }

    func testBlankSearchUsesNormalListAndConcurrentAppendDoesNotFetch() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = " \n "
        await chat.refreshHistory { method, payload in
            XCTAssertEqual(method, "conversations.list")
            XCTAssertEqual(payload["query"] as? String, "")
            XCTAssertEqual(payload["offset"] as? Int, 0)
            return try! JSONSerialization.data(withJSONObject: [
                "conversations": [self.row(1)], "next_offset": 1,
            ])
        }
        XCTAssertEqual(chat.nextOffset, 1)
        chat.historyLoading = true
        var calls = 0
        await chat.refreshHistory(append: true) { _, _ in calls += 1; return nil }
        XCTAssertEqual(calls, 0)
    }

    func testCancelledSearchDoesNotStartAnIPCRequest() async {
        let chat = JarvisChatStore()
        chat.historyQuery = "aguja"
        var ready: CheckedContinuation<Void, Never>?
        var calls = 0
        let task = Task {
            await withCheckedContinuation { ready = $0 }
            await chat.refreshHistory { _, _ in calls += 1; return nil }
        }
        while ready == nil { await Task.yield() }
        task.cancel()
        ready?.resume()
        await task.value
        XCTAssertEqual(calls, 0)
        XCTAssertFalse(chat.historyLoading)
        XCTAssertNil(chat.historyError)
    }

    func testCursorLoopProtectionStorageStaysBounded() async throws {
        let chat = JarvisChatStore()
        chat.historyQuery = "aguja"
        for value in 0..<1_024 { chat.historySearchCursorDigests.insert("previous_\(value)") }
        chat.nextSearchCursor = cursor(1)
        var calls = 0
        await chat.refreshHistory(append: true) { _, _ in
            calls += 1
            return try! self.page(next: self.cursor(calls + 1))
        }
        XCTAssertEqual(calls, 32)
        XCTAssertLessThanOrEqual(chat.historySearchCursorDigests.count, 1_024)
        XCTAssertTrue(chat.historySearchIncomplete)
    }

    func testSameChatMatchWaitsForLayoutAndNavigatesOncePerSelection() async throws {
        let chat = JarvisChatStore()
        let (entry, page) = try matchedHistory()
        try chat.restoreConversation(entry, page: page)
        let first = try XCTUnwrap(chat.historyMatchNavigation)
        await chat.navigateToHistoryMatch(first, afterLayout: {}) { _ in }
        XCTAssertNil(chat.historyMatchNavigation)
        // Reopening the same match must issue a new signal, even though the
        // conversation and the rendered message IDs have not changed.
        try chat.restoreConversation(entry, page: page)
        let reopened = try XCTUnwrap(chat.historyMatchNavigation)
        XCTAssertNotEqual(first.id, reopened.id)
        XCTAssertEqual(first.conversationID, reopened.conversationID)
        var rendered = false
        var layout: CheckedContinuation<Void, Never>?
        var scrolled: [UUID] = []
        let task = Task {
            await chat.navigateToHistoryMatch(reopened, afterLayout: {
                await withCheckedContinuation { layout = $0 }
                rendered = true
            }) { target in
                XCTAssertTrue(rendered)
                XCTAssertTrue(chat.transcript.messages.contains { $0.id == target })
                scrolled.append(target)
            }
        }
        while layout == nil { await Task.yield() }
        XCTAssertTrue(scrolled.isEmpty)
        layout?.resume()
        await task.value
        XCTAssertEqual(scrolled, [id(10)])
        XCTAssertNil(chat.historyMatchNavigation)
        await chat.navigateToHistoryMatch(reopened, afterLayout: {}) { scrolled.append($0) }
        XCTAssertEqual(scrolled, [id(10)])
    }

    func testStaleMatchCannotScrollAfterQueryChatHistoryOrTaskChanges() async throws {
        for change in ["query", "chat", "recent", "new-match", "cancel"] {
            let chat = JarvisChatStore()
            chat.historyQuery = "aguja"
            let (entry, page) = try matchedHistory()
            try chat.restoreConversation(entry, page: page)
            let request = try XCTUnwrap(chat.historyMatchNavigation)
            var layout: CheckedContinuation<Void, Never>?
            var scrolled: [UUID] = []
            let task = Task {
                await chat.navigateToHistoryMatch(request, afterLayout: {
                    await withCheckedContinuation { layout = $0 }
                }) { scrolled.append($0) }
            }
            while layout == nil { await Task.yield() }
            switch change {
            case "query": chat.historyQuery = "otra"
            case "chat": chat.resetConversation(); chat.conversationID = id(2)
            case "recent": chat.transcript.restore([])
            case "new-match": try chat.restoreConversation(entry, page: page)
            default: task.cancel()
            }
            layout?.resume()
            await task.value
            XCTAssertTrue(scrolled.isEmpty, change)
        }
    }
}
