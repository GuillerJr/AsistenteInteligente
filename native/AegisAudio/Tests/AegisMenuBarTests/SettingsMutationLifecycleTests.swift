import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class SettingsMutationLifecycleTests: XCTestCase {
    private let grant = JarvisStandingGrant(tool_name: "synthetic_read", granted_at: "2026-10-22")
    private let app = JarvisAppGrant(bundle_identifier: "test.synthetic", granted_at: "2026-10-22")

    func testBusyStoreDoesNotRemoveAnyRowWithoutSendingTheOperation() async throws {
        var calls = 0
        let store = JarvisSettingsStore { _, _, _ in calls += 1; throw SettingsTestError.offline }
        store.snapshot = try JSONDecoder().decode(JarvisSettingsSnapshot.self, from: SettingsRequests.status)
        let hit = JarvisMemoryHit(memory_id: "synthetic-memory", excerpt: "Sintético")
        let task = JarvisScheduledTask(task_id: "task", title: "Prueba", prompt: "Sintético",
            schedule: "once", notify: "none", enabled: false, next_run_at: nil, last_status: nil)
        let goal = JarvisGoal(goal_id: "goal", title: "Prueba", objective: "Sintético",
            status: "paused", status_label: "Pausado", steps_used: 0, max_steps: 1, notes: [])
        store.standingGrants = [grant]; store.appGrants = [app]; store.memoryHits = [hit]
        store.scheduledTasks = [task]; store.goals = [goal]
        store.busy = true
        await store.revokeStandingGrant(grant)
        await store.revokeAppGrant(app)
        await store.deleteMemory(hit)
        await store.deleteScheduledTask(task)
        await store.deleteGoal(goal)
        XCTAssertEqual(calls, 0)
        XCTAssertEqual(store.standingGrants, [grant]); XCTAssertEqual(store.appGrants, [app])
        XCTAssertEqual(store.memoryHits.map(\.id), [hit.id])
        XCTAssertEqual(store.scheduledTasks, [task]); XCTAssertEqual(store.goals, [goal])
        XCTAssertTrue(store.busy)
    }

    func testDeletionRequiresTypedReceiptAndMemoryIdentity() throws {
        for method in ["jobs.grants.revoke", "jobs.app_grants.revoke", "schedules.delete", "goals.delete"] {
            let key = method.hasSuffix("revoke") ? "revoked" : "deleted"
            for value in ["{}", "{\"\(key)\":1}", "{\"\(key)\":\"true\"}", "{\"state\":\"error\"}"] {
                XCTAssertThrowsError(try JarvisSettingsDeletionReceipt.validate(
                    method: method, payload: [:], data: Data(value.utf8)), method)
            }
            for value in [true, false] {
                XCTAssertNoThrow(try JarvisSettingsDeletionReceipt.validate(method: method,
                    payload: [:], data: Data("{\"\(key)\":\(value)}".utf8)))
            }
        }
        for receipt in ["{}", "{\"memory_id\":\"other\",\"status\":\"deleted\"}",
                        "{\"memory_id\":\"selected\",\"status\":\"pending\"}"] {
            XCTAssertThrowsError(try JarvisSettingsDeletionReceipt.validate(method: "memory.delete",
                payload: ["memory_id": "selected"], data: Data(receipt.utf8)))
        }
    }

    func testMalformedOrFailedRevocationKeepsTheGrant() async {
        for receipt in ["{}", "{\"revoked\":1}", "{\"state\":\"error\"}"] {
            let store = JarvisSettingsStore { _, _, _ in Data(receipt.utf8) }
            store.standingGrants = [grant]
            await store.revokeStandingGrant(grant)
            XCTAssertEqual(store.standingGrants, [grant]); XCTAssertTrue(store.failed)
        }
        let store = JarvisSettingsStore { _, _, _ in throw SettingsTestError.offline }
        store.appGrants = [app]
        await store.revokeAppGrant(app)
        XCTAssertEqual(store.appGrants, [app]); XCTAssertTrue(store.failed)
    }

    func testConfirmedRevocationSurvivesAnUnrelatedRefreshFailure() async {
        let store = JarvisSettingsStore { method, _, _ in
            if method == "jobs.grants.revoke" { return Data("{\"revoked\":true}".utf8) }
            throw SettingsTestError.offline
        }
        store.standingGrants = [grant]
        await store.revokeStandingGrant(grant)
        XCTAssertTrue(store.standingGrants.isEmpty)
        XCTAssertTrue(store.failed)
        XCTAssertEqual(store.failureSource, .configurationRead)
    }

    func testOldListCannotRestoreARevokedGrant() async {
        let requests = SettingsRequests(paused: "jobs.grants.list")
        let store = JarvisSettingsStore(transport: requests.call)
        store.standingGrants = [grant]
        let load = Task { await store.loadStandingGrants() }
        await requests.waitUntilStarted()
        await store.revokeStandingGrant(grant)
        requests.finish(Data("{\"grants\":[{\"tool_name\":\"synthetic_read\",\"granted_at\":\"old\"}]}".utf8))
        await load.value
        XCTAssertTrue(store.standingGrants.isEmpty)
    }

    func testCancelBeforeStartResponseDoesNotCreateAnOldMonitor() async {
        let requests = SettingsRequests(paused: "settings.start")
        let store = JarvisSettingsStore(transport: requests.call)
        let operation = Task { await store.perform("settings.start", success: "No debe mostrarse") }
        await requests.waitUntilStarted()
        await store.cancelOperation()
        let message = store.message
        requests.finish(Data("{\"state\":\"starting\"}".utf8))
        let completed = await operation.value
        XCTAssertFalse(completed); XCTAssertFalse(store.busy)
        XCTAssertEqual(store.operationState, "idle"); XCTAssertEqual(store.message, message)
        XCTAssertFalse(requests.calls.contains("settings.operation"))
    }

    func testCancelledMonitorCannotAnnounceItsLateSuccess() async {
        let requests = SettingsRequests(paused: "settings.operation")
        let store = JarvisSettingsStore(transport: requests.call)
        await store.perform("settings.start", success: "Éxito obsoleto")
        await requests.waitUntilStarted()
        await store.cancelOperation()
        let message = store.message
        requests.finish(Data("{\"state\":\"done\"}".utf8))
        // The continuation resumes a main-actor task; yield to process its receipt.
        for _ in 0..<20 { await Task.yield() }
        XCTAssertFalse(store.busy); XCTAssertEqual(store.operationState, "idle")
        XCTAssertEqual(store.message, message)
    }

    func testOldSettingsReadIsDiscardedAndQueuedRefreshConvergesAfterMutation() async {
        let requests = SettingsRequests(paused: "settings.status")
        let store = JarvisSettingsStore(transport: requests.call)
        let load = Task { await store.refresh() }
        await requests.waitUntilStarted()
        await store.revokeStandingGrant(grant)
        requests.finish(SettingsRequests.status)
        await load.value
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(requests.calls.filter { $0 == "settings.status" }.count, 2)
        XCTAssertEqual(store.snapshot?.memory_namespace, "synthetic")
        XCTAssertFalse(store.failed)
    }

    func testOldExtensionsReadIsDiscardedAndQueuedRefreshConvergesAfterMutation() async {
        let requests = SettingsRequests(paused: "extensions.status")
        let store = JarvisSettingsStore(transport: requests.call)
        let load = Task { await store.refreshExtensions() }
        await requests.waitUntilStarted()
        await store.revokeStandingGrant(grant)
        requests.finish(Data("{\"skills\":[],\"plugins\":[],\"tool_options\":{},\"busy\":false,\"restart_required\":true}".utf8))
        await load.value
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(requests.calls.filter { $0 == "extensions.status" }.count, 2)
        XCTAssertEqual(store.extensionsSnapshot?.restart_required, false)
        XCTAssertNil(store.extensionsError)
    }
}

private enum SettingsTestError: Error { case offline }

@MainActor
private final class SettingsRequests {
    static let status = Data("""
        {"models":{"version":1,"models":[],"roles":{}},"models_valid":true,
         "credential":"configured","servers":[],"restart_required":false,
         "memory_namespace":"synthetic"}
        """.utf8)
    let paused: String
    var calls: [String] = []
    private var continuation: CheckedContinuation<Data, any Error>?
    private var arrival: CheckedContinuation<Void, Never>?
    private var hasPaused = false

    init(paused: String) { self.paused = paused }

    func call(_ method: String, _ payload: [String: Any], _ timeout: TimeInterval) async throws -> Data {
        calls.append(method)
        if method == paused, !hasPaused {
            hasPaused = true
            return try await withCheckedThrowingContinuation {
                continuation = $0
                arrival?.resume(); arrival = nil
            }
        }
        switch method {
        case "settings.status": return Self.status
        case "extensions.status": return Data("{\"skills\":[],\"plugins\":[],\"tool_options\":{},\"busy\":false,\"restart_required\":false}".utf8)
        case "settings.start": return Data("{\"state\":\"starting\"}".utf8)
        case "jobs.grants.revoke": return Data("{\"revoked\":true}".utf8)
        case "settings.cancel": return Data("{\"state\":\"cancelled\"}".utf8)
        default: throw SettingsTestError.offline
        }
    }

    func waitUntilStarted() async {
        if continuation == nil { await withCheckedContinuation { arrival = $0 } }
    }

    func finish(_ data: Data) {
        continuation?.resume(returning: data); continuation = nil
    }
}
