import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

private actor PendingProductWire {
    private var starts = 0
    private var reads = 0
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        if method == "product.tool.start" { starts += 1; throw RPCFailure.disconnected }
        if method == "runs.get" { reads += 1; throw RPCFailure.remote(-32010, "run_not_found") }
        throw RPCFailure.invalidResponse
    }
    var counts: (Int, Int) { (starts, reads) }
}

final class ProductRequestCustodyTests: XCTestCase {
    @MainActor func testLostStartACKSurvivesAtoBAndNotFoundNeverReleasesHandoffCustody() async throws {
        let store = ChatStore(), wire = PendingProductWire()
        let first = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        let second = SessionScope(sessionID: String(repeating: "b", count: 32), temporary: false)
        func session(_ scope: SessionScope) throws -> ChatSession {
            try XCTUnwrap(ChatSession(.object(["session_id": .string(scope.sessionID),
                "temporary": .bool(false), "title": .string("Chat sintético"), "archived": .bool(false),
                "agent_id": .string("jarvis"), "parent_session_id": .null])))
        }
        store.sessions = try [session(first), session(second)]
        store.selectedID = first.sessionID; store.ready = true; store.connected = true
        store.productProjects.configure(request: { try await wire.request($0, $1) })
        store.productProjects.setScope(first)
        _ = await store.productProjects.register(path: "/synthetic/explicit-repository", name: "Sintético")
        let requestID = try XCTUnwrap(store.productProjects.pendingRequestID)
        XCTAssertFalse(store.mayHandoff)
        // park/create/select all call this same custody boundary before their
        // stores are rebound. Simulate only the viewer's A→B switch, no server.
        store.retainProductRequestCustody()
        store.selectedID = second.sessionID; store.runTracker.select(second)
        store.productProjects.setScope(second); store.retainProductRequestCustody()
        XCTAssertTrue(store.hasBackgroundRuns); XCTAssertFalse(store.mayHandoff)
        XCTAssertEqual(store.runTracker.observations[first]?.runID, requestID)
        XCTAssertEqual(store.runTracker.observations[first]?.acceptanceUnconfirmed, true)
        let absent = await store.runTracker.refresh(scope: first, runID: requestID,
            request: { try await wire.request($0, $1) })
        XCTAssertNil(absent); XCTAssertTrue(store.runTracker.hasActiveRuns)
        XCTAssertFalse(store.mayHandoff)
        let counts = await wire.counts; XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 1)
        let terminal: RPCValue = .object(["run": .object(["run_id": .string(requestID),
            "status": .string("completed"), "outcome": .string("known"),
            "action_receipts": .array([]), "error": .null])])
        let observed = await store.runTracker.refresh(scope: first, runID: requestID,
            request: { method, params in
                XCTAssertEqual(method, "runs.get"); XCTAssertEqual(params["run_id"], .string(requestID))
                return terminal
            })
        let receipt = try XCTUnwrap(observed)
        store.productProjects.observeRunReceipt(receipt, scope: first)
        XCTAssertFalse(store.runTracker.hasActiveRuns)
        XCTAssertTrue(store.productProjects.unsettledRequests.isEmpty)
        XCTAssertTrue(store.mayHandoff)
        store.productProjects.close(); store.runtime.retire()
    }

    @MainActor func testHeadlessNotificationAdapterDoesNotCreateSystemCenterOrAskPermission() async throws {
        guard Bundle.main.bundleURL.pathExtension.lowercased() != "app" || Bundle.main.bundleIdentifier == nil else {
            throw XCTSkip("Este caso exige el ejecutor sin bundle de app; no solicita TCC en una app anfitriona.")
        }
        let center = MacProductNotificationCenter()
        let permission = try await center.requestPermission()
        let allowed = await center.canDeliver()
        let ids = await center.existingIdentifiers(namespace: String(repeating: "a", count: 64))
        XCTAssertFalse(permission); XCTAssertFalse(allowed); XCTAssertTrue(ids.isEmpty)
        center.remove(identifiers: ["jarvis.product.synthetic-notification"])
        do {
            try await center.add(identifier: "jarvis.product.synthetic-notification", title: "Sintético",
                body: "No debe publicarse", namespace: String(repeating: "a", count: 64))
            XCTFail("Un ejecutor sin app debe rechazar el efecto sin inicializar UNUserNotificationCenter.")
        } catch { XCTAssertEqual(error as? RPCFailure, .unavailable) }
    }
}
