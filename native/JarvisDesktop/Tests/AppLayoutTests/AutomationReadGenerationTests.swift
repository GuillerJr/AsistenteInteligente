import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

private actor AutomationReadGate {
    private var held = false
    private var released = false
    private var continuation: CheckedContinuation<Void, Never>?
    private var calls = 0
    func request() async -> RPCValue {
        calls += 1; held = true
        if !released { await withCheckedContinuation { continuation = $0 } }
        return .object(["hooks": .array([]), "flows": .array([]),
            "configuration_hash": .string(String(repeating: "b", count: 64)),
            "workspace_id": .string(String(repeating: "c", count: 64)),
            "runner_running": .bool(false), "webhook": .object(["running": .bool(false)])])
    }
    func waitUntilHeld() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while !held {
            try Task.checkCancellation()
            guard ContinuousClock.now < deadline else { throw RPCFailure.unavailable }
            await Task.yield()
        }
    }
    func release() { released = true; continuation?.resume(); continuation = nil }
    func count() -> Int { calls }
}

final class AutomationReadGenerationTests: XCTestCase {
    @MainActor func testOldAutomationCatalogCannotRepopulateAfterProfileTransportChanges() async throws {
        let store = ManagementStore(), gate = AutomationReadGate()
        let oldClient = ManagementClient(request: { _, _ in await gate.request() })
        store.configure(oldClient, runtimeIsActive: { true })
        let pending = Task { @MainActor in await store.refreshAutomation() }
        do { try await gate.waitUntilHeld() }
        catch { await gate.release(); _ = await pending.value; throw error }
        let replacement = ManagementClient(request: { _, _ in throw RPCFailure.unavailable })
        store.configure(replacement, runtimeIsActive: { true })
        await gate.release(); _ = await pending.value
        XCTAssertNil(store.automationSnapshot)
        XCTAssertNil(store.errorMessage)
        XCTAssertFalse(store.automationLoading)
        await store.close()
    }

    @MainActor func testReadingDuringMutationCannotOverwriteReviewedAutomationCatalog() async throws {
        let store = ManagementStore(), gate = AutomationReadGate()
        store.configure(ManagementClient(request: { _, _ in await gate.request() }), runtimeIsActive: { true })
        store.isChanging = true
        await store.refreshAutomation()
        let calls = await gate.count()
        XCTAssertEqual(calls, 0)
        XCTAssertNil(store.automationSnapshot)
        store.isChanging = false
        await gate.release()
        await store.refreshAutomation()
        XCTAssertNotNil(store.automationSnapshot)
        await store.close()
    }
}
