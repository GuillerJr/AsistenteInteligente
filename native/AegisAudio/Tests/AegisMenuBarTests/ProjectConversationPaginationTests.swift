import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class ProjectConversationPaginationTests: XCTestCase {
    private let firstProject = UUID().uuidString.lowercased()
    private let otherProject = UUID().uuidString.lowercased()
    private let firstCursor = "first_page." + String(repeating: "a", count: 64)
    private let secondCursor = "second_page." + String(repeating: "b", count: 64)

    private func page(_ ids: [Int], project: String, cursor: String? = nil) throws -> Data {
        let rows: [[String: Any]] = ids.map { index in
            ["conversation_id": String(format: "00000000-0000-0000-0000-%012d", index),
             "title": "Chat sintético \(index)", "updated_at": "2026-10-22T12:00:00Z",
             "archived": false, "project_id": project]
        }
        return try JSONSerialization.data(withJSONObject: [
            "project_id": project, "conversations": rows, "next_cursor": cursor as Any? ?? NSNull(),
        ])
    }

    func testLoadsMoreThan50ProjectChatsWithoutDuplicatesAndStopsAtLastPage() async throws {
        let store = ProjectConversationStore()
        let pages = [
            try page(Array(1...30), project: firstProject, cursor: firstCursor),
            try page(Array(31...60), project: firstProject, cursor: secondCursor),
            try page(Array(61...73), project: firstProject),
        ]
        var requests: [[String: Any]] = []
        func fetch(_ payload: [String: Any]) async -> Data? {
            requests.append(payload)
            return pages[requests.count - 1]
        }
        await store.load(projectID: firstProject, fetch: fetch)
        await store.load(projectID: firstProject, append: true, fetch: fetch)
        await store.load(projectID: firstProject, append: true, fetch: fetch)
        await store.load(projectID: firstProject, append: true, fetch: fetch)

        XCTAssertEqual(store.conversations.count, 73)
        XCTAssertEqual(Set(store.conversations.map(\.id)).count, 73)
        XCTAssertEqual(store.conversations.first?.title, "Chat sintético 1")
        XCTAssertEqual(store.conversations.last?.title, "Chat sintético 73")
        XCTAssertEqual(requests.count, 3)
        XCTAssertNil(requests[0]["cursor"])
        XCTAssertEqual(requests[1]["cursor"] as? String, firstCursor)
        XCTAssertEqual(requests[2]["cursor"] as? String, secondCursor)
        XCTAssertTrue(requests.allSatisfy { $0["project_id"] as? String == firstProject })
        XCTAssertNil(store.nextCursor)
        XCTAssertNil(store.error)
        XCTAssertFalse(store.loading)
    }

    func testOverlappingPagesDeduplicateWithoutReorderingExistingRows() async throws {
        let store = ProjectConversationStore()
        let first = try page([1, 2], project: firstProject, cursor: firstCursor)
        await store.load(projectID: firstProject) { _ in first }
        let next = try page([2, 3], project: firstProject)
        await store.load(projectID: firstProject, append: true) { _ in next }
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 1", "Chat sintético 2", "Chat sintético 3"])
    }

    func testInvalidPageRejectsWrongProjectRepeatedRowsAndMalformedCursor() throws {
        XCTAssertThrowsError(try ProjectConversationPage(
            page([1], project: otherProject), projectID: firstProject
        ))
        XCTAssertThrowsError(try ProjectConversationPage(
            page([1, 1], project: firstProject), projectID: firstProject
        ))
        XCTAssertThrowsError(try ProjectConversationPage(
            page(Array(1...51), project: firstProject), projectID: firstProject
        ))
        for badCursor in ["", "wrong", "a." + String(repeating: "g", count: 64),
                          String(repeating: "x", count: 1_025)] {
            XCTAssertThrowsError(try ProjectConversationPage(
                page([1], project: firstProject, cursor: badCursor), projectID: firstProject
            ))
        }
        XCTAssertThrowsError(try ProjectConversationPage(
            page([], project: firstProject, cursor: firstCursor), projectID: firstProject
        ))
    }

    func testResponseRowsMustBelongToTheRequestedProjectAndRemainVisible() throws {
        let good = try page([1], project: firstProject)
        var payload = try XCTUnwrap(JSONSerialization.jsonObject(with: good) as? [String: Any])
        var rows = try XCTUnwrap(payload["conversations"] as? [[String: Any]])
        rows[0]["project_id"] = otherProject
        payload["conversations"] = rows
        XCTAssertThrowsError(try ProjectConversationPage(
            JSONSerialization.data(withJSONObject: payload), projectID: firstProject
        ))
        rows[0]["project_id"] = firstProject
        rows[0]["archived"] = true
        payload["conversations"] = rows
        XCTAssertThrowsError(try ProjectConversationPage(
            JSONSerialization.data(withJSONObject: payload), projectID: firstProject
        ))
    }

    func testFailedAppendPreservesRowsAndCursorButRefreshCanRecover() async throws {
        let store = ProjectConversationStore()
        let first = try page([1], project: firstProject, cursor: firstCursor)
        await store.load(projectID: firstProject) { _ in first }
        await store.load(projectID: firstProject, append: true) { _ in nil }
        XCTAssertEqual(store.conversations.count, 1)
        XCTAssertEqual(store.nextCursor, firstCursor)
        XCTAssertNotNil(store.error)
        let repeated = try page([2], project: firstProject, cursor: firstCursor)
        await store.load(projectID: firstProject, append: true) { _ in repeated }
        XCTAssertEqual(store.conversations.count, 1, "Un cursor inmóvil no puede crear un bucle")
        XCTAssertNotNil(store.error)
        let refreshed = try page([3], project: firstProject)
        await store.load(projectID: firstProject) { payload in
            XCTAssertNil(payload["cursor"])
            return refreshed
        }
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 3"])
        XCTAssertNil(store.error)
        XCTAssertNil(store.nextCursor)
    }

    func testLatePageCannotOverwriteAnotherProjectAndConcurrentAppendIsIgnored() async throws {
        let store = ProjectConversationStore()
        let first = try page([1], project: firstProject, cursor: firstCursor)
        await store.load(projectID: firstProject) { _ in first }
        var pending: CheckedContinuation<Data?, Never>?
        let oldProject = firstProject
        let loading = Task {
            await store.load(projectID: oldProject, append: true) { _ in
                await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        var duplicateRequests = 0
        await store.load(projectID: firstProject, append: true) { _ in
            duplicateRequests += 1
            return first
        }
        XCTAssertEqual(duplicateRequests, 0)
        let other = try page([9], project: otherProject)
        await store.load(projectID: otherProject) { _ in other }
        pending?.resume(returning: try page([2], project: firstProject))
        await loading.value
        XCTAssertEqual(store.projectID, otherProject)
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 9"])
        XCTAssertNil(store.nextCursor)
        XCTAssertNil(store.error)
        XCTAssertFalse(store.loading)
    }

    func testNonAdjacentCursorCyclePreservesPageAndCanRetryWithoutRepeatingRows() async throws {
        let store = ProjectConversationStore()
        let first = try page([1], project: firstProject, cursor: firstCursor)
        let second = try page([2], project: firstProject, cursor: secondCursor)
        await store.load(projectID: firstProject) { _ in first }
        await store.load(projectID: firstProject, append: true) { _ in second }

        let cycle = try page([3], project: firstProject, cursor: firstCursor)
        await store.load(projectID: firstProject, append: true) { payload in
            XCTAssertEqual(payload["cursor"] as? String, secondCursor)
            return cycle
        }
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 1", "Chat sintético 2"])
        XCTAssertEqual(store.nextCursor, secondCursor)
        XCTAssertNotNil(store.error)
        XCTAssertFalse(store.loading)

        let finalPage = try page([3], project: firstProject)
        await store.load(projectID: firstProject, append: true) { payload in
            XCTAssertEqual(payload["cursor"] as? String, secondCursor)
            return finalPage
        }
        XCTAssertEqual(store.conversations.map(\.title),
                       ["Chat sintético 1", "Chat sintético 2", "Chat sintético 3"])
        XCTAssertNil(store.nextCursor)
        XCTAssertNil(store.error)
    }

    func testRefreshAndProjectSwitchResetTheCursorHistory() async throws {
        let store = ProjectConversationStore()
        let first = try page([1], project: firstProject, cursor: firstCursor)
        let second = try page([2], project: firstProject, cursor: secondCursor)
        await store.load(projectID: firstProject) { _ in first }
        await store.load(projectID: firstProject, append: true) { _ in second }
        await store.load(projectID: firstProject) { _ in first }
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 1"])
        XCTAssertEqual(store.nextCursor, firstCursor)
        XCTAssertNil(store.error)
        await store.load(projectID: firstProject, append: true) { _ in second }
        XCTAssertEqual(store.conversations.count, 2)
        XCTAssertNil(store.error)

        let other = try page([9], project: otherProject, cursor: firstCursor)
        await store.load(projectID: otherProject) { _ in other }
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 9"])
        XCTAssertEqual(store.nextCursor, firstCursor)
        XCTAssertNil(store.error)
    }

    func testLateResponseCannotAddCursorsToTheNewGeneration() async throws {
        let store = ProjectConversationStore()
        var pending: CheckedContinuation<Data?, Never>?
        let oldProject = firstProject
        let loading = Task {
            await store.load(projectID: oldProject) { _ in
                await withCheckedContinuation { pending = $0 }
            }
        }
        while pending == nil { await Task.yield() }
        let other = try page([9], project: otherProject, cursor: firstCursor)
        await store.load(projectID: otherProject) { _ in other }
        pending?.resume(returning: try page([1], project: firstProject, cursor: secondCursor))
        await loading.value
        let next = try page([10], project: otherProject, cursor: secondCursor)
        await store.load(projectID: otherProject, append: true) { _ in next }
        XCTAssertEqual(store.projectID, otherProject)
        XCTAssertEqual(store.conversations.map(\.title), ["Chat sintético 9", "Chat sintético 10"])
        XCTAssertEqual(store.nextCursor, secondCursor)
        XCTAssertNil(store.error)
    }
}
