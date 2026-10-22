import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

final class SidebarHistoryTests: XCTestCase {
    private func row(_ number: Int, _ timestamp: String,
                     match: ChatSearchMatch? = nil) -> ChatConversationSummary {
        ChatConversationSummary(
            id: UUID(uuidString: String(format: "00000000-0000-0000-0000-%012d", number))!,
            title: "Chat sintético \(number)", updatedAt: timestamp,
            archived: false, searchMatch: match
        )
    }

    private func calendar(_ zone: String = "America/Guayaquil") -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: zone)!
        return calendar
    }

    func testLocalMidnightAndSevenCalendarDaysDefineGroups() throws {
        let now = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-22T17:00:00Z"))
        let entries = [
            row(1, "2026-10-22T05:00:00.123Z"),
            row(2, "2026-10-22T04:59:59Z"),
            row(3, "2026-10-21T04:59:59Z"),
            row(4, "2026-10-16T05:00:00Z"),
            row(5, "2026-10-16T04:59:59Z"),
        ]
        let groups = JarvisSidebarHistory.groups(entries, query: "", archived: false, trash: false,
                                                now: now, calendar: calendar())
        XCTAssertEqual(groups.map(\.period), [.today, .yesterday, .week, .earlier])
        XCTAssertEqual(groups.map { $0.entries.map(\.id) }, [
            [entries[0].id], [entries[1].id], [entries[2].id, entries[3].id], [entries[4].id],
        ])
    }

    func testDaylightSavingUsesCalendarDayInsteadOfLast24Hours() throws {
        let now = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-03-09T04:30:00Z"))
        let yesterday = row(1, "2026-03-08T05:15:00Z")
        let groups = JarvisSidebarHistory.groups([yesterday], query: "", archived: false, trash: false,
                                                now: now, calendar: calendar("America/New_York"))
        // It is 00:30 Monday after a 23-hour Sunday; 00:15 Sunday is yesterday.
        XCTAssertEqual(groups.map(\.period), [.yesterday])
    }

    func testInvalidDatesRemainReachableAndFutureClockDriftRemainsVisible() throws {
        let now = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-22T17:00:00Z"))
        let entries = [row(1, "invalid"), row(2, "2027-01-01T00:00:00Z")]
        let groups = JarvisSidebarHistory.groups(entries, query: "", archived: false, trash: false,
                                                now: now, calendar: calendar())
        XCTAssertEqual(groups.map(\.period), [.today, .earlier])
        XCTAssertEqual(Set(groups.flatMap(\.entries).map(\.id)), Set(entries.map(\.id)))
    }

    func testSearchArchiveAndTrashRetainServerOrderAndMatchingTurn() throws {
        let match = ChatSearchMatch(turnID: UUID(), sequence: 7, excerpt: "Coincidencia sintética")
        let entries = [row(1, "2025-01-01T00:00:00Z", match: match), row(2, "2026-10-22T17:00:00Z")]
        for filter in [("  buscar  ", false, false), ("", true, false), ("", false, true)] {
            let groups = JarvisSidebarHistory.groups(entries, query: filter.0, archived: filter.1,
                                                    trash: filter.2)
            XCTAssertEqual(groups.count, 1)
            XCTAssertEqual(groups.first?.entries, entries)
            XCTAssertEqual(groups.first?.entries.first?.searchMatch, match)
        }
    }

    func testAppendingPagesPreservesExistingIdentityAndWithinGroupOrder() throws {
        let now = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-22T17:00:00Z"))
        let first = [row(1, "2026-10-22T16:00:00Z"), row(2, "2026-10-21T16:00:00Z")]
        let more = [row(3, "2026-10-21T15:00:00Z"), row(4, "2025-10-21T15:00:00Z")]
        let before = JarvisSidebarHistory.groups(first, query: "", archived: false, trash: false,
                                                now: now, calendar: calendar())
        let after = JarvisSidebarHistory.groups(first + more, query: "", archived: false, trash: false,
                                               now: now, calendar: calendar())
        XCTAssertEqual(before.map(\.id), [.today, .yesterday])
        XCTAssertEqual(after.map(\.id), [.today, .yesterday, .earlier])
        XCTAssertEqual(after[1].entries.map(\.id), [first[1].id, more[0].id])
        XCTAssertEqual(after.flatMap(\.entries).count, 4)
        XCTAssertTrue(JarvisSidebarHistory.isSelected(after[1].entries[0], conversationID: first[1].id,
                                                     showingConversation: true, trash: false))
        XCTAssertFalse(JarvisSidebarHistory.isSelected(after[1].entries[0], conversationID: first[1].id,
                                                      showingConversation: false, trash: false))
        XCTAssertFalse(JarvisSidebarHistory.isSelected(after[1].entries[0], conversationID: first[1].id,
                                                      showingConversation: true, trash: true))
    }

    func testEmptyHistoryProducesNoEmptyGroups() {
        XCTAssertTrue(JarvisSidebarHistory.groups([], query: "", archived: false, trash: false).isEmpty)
    }
}
