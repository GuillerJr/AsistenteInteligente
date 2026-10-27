import Foundation
import XCTest
@testable import DesktopCore

private enum MemoryIntelligenceFixture {
    static let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
    static let other = SessionScope(sessionID: String(repeating: "b", count: 32), temporary: false)
    static let binding = String(repeating: "c", count: 64)
    static let model = String(repeating: "d", count: 64)
    static let reviewID = String(repeating: "e", count: 32)
    static func object(_ value: RPCValue, replacing: [String: RPCValue]) -> RPCValue {
        .object(value.object!.merging(replacing, uniquingKeysWith: { _, next in next }))
    }
    static func identity(scope: SessionScope = scope) -> RPCValue {
        .object(["client_id": .string(String(repeating: "1", count: 64)), "agent_id": .string("jarvis"),
            "workspace_id": .string(String(repeating: "2", count: 64)), "temporary": .bool(scope.temporary),
            "session_id": scope.temporary ? .string(scope.sessionID) : .null])
    }
    static func policy(scope: SessionScope = scope, revision: Int = 1,
                       settings: MemoryIntelligenceSettings = .init()) throws -> RPCValue {
        let unsigned = RPCValue.object(["profile_binding": .string(binding), "scope": identity(scope: scope),
            "revision": .number(Double(revision)), "settings": settings.value, "permissions_portable": .bool(false)])
        return object(unsigned, replacing: ["digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(unsigned)))])
    }
    static func review(policy: RPCValue, settings: RPCValue) throws -> RPCValue {
        let metadata = RPCValue.object(["kind": .string("memory.policy.configure"), "review_id": .string(reviewID),
            "profile_binding": policy["profile_binding"], "scope": policy["scope"], "before_revision": policy["revision"],
            "before_digest": policy["digest"], "settings": settings, "source_resources_opened": .bool(false),
            "permissions_portable": .bool(false)])
        return .object(["review_id": .string(reviewID), "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata))),
            "expires_in_seconds": .number(120), "metadata": metadata])
    }
    static func job(id: String, kind: String = "model_prepare", phase: String = "running", revision: Int = 1,
                    generation: Int? = nil) -> RPCValue {
        .object(["id": .string(id), "kind": .string(kind), "phase": .string(phase), "error": .null,
            "source_generation": generation.map { .number(Double($0)) } ?? .null,
            "policy_revision": .number(Double(revision)),
            "progress": .object(["documents_done": .number(0), "documents_total": .number(2), "chunks": .number(0)]),
            "receipt": .null])
    }
    static func status(scope: SessionScope = scope, revision: Int = 1, settings: MemoryIntelligenceSettings = .init(),
                       job: RPCValue = .null, previous: RPCValue? = nil) throws -> RPCValue {
        let envelope = RPCValue.object(["intelligence": .object([
            "scope": .object(scope.params), "policy": try policy(scope: scope, revision: revision, settings: settings),
            "model": .object(["id": .string("sentence-transformers/all-MiniLM-L6-v2"), "state": .string("available"),
                "available": .bool(true), "runtime_available": .bool(true), "bytes": .number(92_000_000),
                "fingerprint": .string(model), "cpu_only": .bool(true), "network_for_inference": .bool(false)]),
            "index": .object(["state": .string("ready"), "source_generation": .number(3), "generation": .number(3),
                "document_count": .number(2), "chunk_count": .number(3), "bytes": .number(8192),
            "model_fingerprint": .string(model), "error": .null]), "current_job": job, "last_recall": .null])])
        guard let previous else { return envelope }
        let modelValue = object(envelope["intelligence"]["model"], replacing: ["previous_copy": previous])
        return .object(["intelligence": object(envelope["intelligence"], replacing: ["model": modelValue])])
    }
}

private actor MemoryIntelligenceBarrier {
    private var entered = false
    private var entryWaiters: [CheckedContinuation<Void, Never>] = []
    private var release: CheckedContinuation<Void, Never>?
    func suspend() async {
        entered = true; entryWaiters.forEach { $0.resume() }; entryWaiters = []
        await withCheckedContinuation { release = $0 }
    }
    func waitForEntry() async {
        if !entered { await withCheckedContinuation { entryWaiters.append($0) } }
    }
    func resume() { release?.resume(); release = nil }
}

private actor MemoryIntelligenceWire {
    private var calls: [(String, [String: RPCValue])] = []
    private var revision = 1
    private var settings = MemoryIntelligenceSettings()
    private var selectedSettings = MemoryIntelligenceSettings()
    private var currentJob = RPCValue.null
    private var losePrepare = false
    private var loseApply = false
    private var loseRetire = false
    private var previous: RPCValue?
    private var notOpened = false
    private var suspendStatus: MemoryIntelligenceBarrier?
    func setLosePrepare() { losePrepare = true }
    func setLoseApply() { loseApply = true }
    func setLoseRetire() { loseRetire = true }
    func setPrevious(_ id: String?) { previous = id.map { .object(["id": .string($0), "bytes": .number(1024)]) } }
    func setNotOpened() { notOpened = true }
    func setBarrier(_ value: MemoryIntelligenceBarrier) { suspendStatus = value }
    func count(_ suffix: String) -> Int { calls.filter { $0.0 == "memory.intelligence." + suffix }.count }
    func params(_ suffix: String) -> [String: RPCValue]? { calls.last { $0.0 == "memory.intelligence." + suffix }?.1 }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append((method, params))
        let scope = SessionScope(sessionID: params["session_id"]!.string!, temporary: params["temporary"]!.bool!)
        switch method {
        case "memory.intelligence.status":
            var captured = try MemoryIntelligenceFixture.status(scope: scope, revision: revision, settings: settings, job: currentJob, previous: previous)
            if notOpened {
                let index = RPCValue.object(["state": .string("not_opened"), "source_generation": .number(0),
                    "generation": .null, "document_count": .number(0), "chunk_count": .number(0), "bytes": .number(0),
                    "model_fingerprint": .null, "error": .null])
                captured = .object(["intelligence": MemoryIntelligenceFixture.object(captured["intelligence"], replacing: ["index": index])])
            }
            if let barrier = suspendStatus { suspendStatus = nil; await barrier.suspend() }
            return captured
        case "memory.intelligence.policy.review":
            let policy = try MemoryIntelligenceFixture.policy(scope: scope, revision: revision, settings: settings)
            XCTAssertEqual(params["expected_revision"], policy["revision"]); XCTAssertEqual(params["expected_digest"], policy["digest"])
            selectedSettings = try MemoryIntelligenceSettings(params["settings"]!)
            return .object(["review": try MemoryIntelligenceFixture.review(policy: policy, settings: selectedSettings.value)])
        case "memory.intelligence.policy.apply":
            XCTAssertEqual(params["review_id"], .string(MemoryIntelligenceFixture.reviewID))
            settings = selectedSettings; revision += 1
            if loseApply { throw RPCFailure.disconnected }
            return try MemoryIntelligenceFixture.status(scope: scope, revision: revision, settings: settings, job: currentJob, previous: previous)
        case "memory.intelligence.model.prepare":
            currentJob = MemoryIntelligenceFixture.job(id: params["request_id"]!.string!, revision: revision)
            if losePrepare { throw RPCFailure.disconnected }; return .object(["job": currentJob])
        case "memory.intelligence.model.retire":
            XCTAssertEqual(params["copy_id"], previous?["id"])
            currentJob = MemoryIntelligenceFixture.job(id: params["request_id"]!.string!, kind: "model_retire", revision: revision)
            if loseRetire { throw RPCFailure.disconnected }; return .object(["job": currentJob])
        case "memory.intelligence.index.rebuild":
            XCTAssertEqual(params["expected_generation"], .number(3))
            XCTAssertEqual(params["expected_policy_revision"], .number(Double(revision)))
            XCTAssertEqual(params["expected_model_fingerprint"], .string(MemoryIntelligenceFixture.model))
            currentJob = MemoryIntelligenceFixture.job(id: params["request_id"]!.string!, kind: "index_rebuild", revision: revision, generation: 3)
            return .object(["job": currentJob])
        case "memory.intelligence.job.get":
            guard params["job_id"] == currentJob["id"] else { throw RPCFailure.remote(-32001, "memory_job_not_found") }
            return .object(["job": currentJob])
        case "memory.intelligence.job.cancel":
            currentJob = MemoryIntelligenceFixture.object(currentJob, replacing: ["phase": .string("cleanup_pending")])
            return .object(["job": currentJob])
        case "memory.intelligence.index.verify":
            return .object(["verified": .bool(true), "generation": .number(3), "index_fingerprint": .string(String(repeating: "f", count: 64))])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class MemoryIntelligenceTests: XCTestCase {
    func testPolicyDigestAndSourcePolicyCannotBeChangedSilently() throws {
        let policy = try MemoryIntelligenceFixture.policy()
        let parsed = try MemoryPolicySnapshot(policy)
        XCTAssertEqual(parsed.settings, .init()); XCTAssertEqual(parsed.revision, 1)
        for replacement: [String: RPCValue] in [["permissions_portable": .bool(true)], ["revision": .number(2)],
            ["settings": .object(["auto_recall_enabled": .bool(true), "mode": .string("nvidia"),
                "auto_index_enabled": .bool(true), "source_policy": .string("scan_workspace")])]] {
            XCTAssertThrowsError(try MemoryPolicySnapshot(MemoryIntelligenceFixture.object(policy, replacing: replacement)))
        }
    }
    func testStatusRejectsCrossChatTemporalAndNetworkInferenceClaims() throws {
        let status = try MemoryIntelligenceFixture.status()
        XCTAssertThrowsError(try MemoryIntelligenceStatus(status, expectedScope: MemoryIntelligenceFixture.other))
        let temporary = SessionScope(sessionID: MemoryIntelligenceFixture.scope.sessionID, temporary: true)
        let wrongPolicy = MemoryIntelligenceFixture.object(status["intelligence"], replacing: ["scope": .object(temporary.params)])
        XCTAssertThrowsError(try MemoryIntelligenceStatus(.object(["intelligence": wrongPolicy]), expectedScope: temporary))
        for key in ["cpu_only", "network_for_inference"] {
            let model = MemoryIntelligenceFixture.object(status["intelligence"]["model"], replacing: [key: .bool(key == "network_for_inference")])
            let row = MemoryIntelligenceFixture.object(status["intelligence"], replacing: ["model": model])
            XCTAssertThrowsError(try MemoryIntelligenceStatus(.object(["intelligence": row]), expectedScope: MemoryIntelligenceFixture.scope))
        }
    }
    func testReviewValidatesItsOwnDigestAndExactCAS() throws {
        let policy = try MemoryIntelligenceFixture.policy()
        let settings = MemoryIntelligenceSettings(autoRecallEnabled: true, mode: .hybridLocal, autoIndexEnabled: true)
        let value = try MemoryIntelligenceFixture.review(policy: policy, settings: settings.value)
        let parsed = try MemoryPolicyChangeReview(value)
        XCTAssertTrue(parsed.matches(try MemoryPolicySnapshot(policy)))
        XCTAssertFalse(parsed.matches(try MemoryPolicySnapshot(MemoryIntelligenceFixture.policy(revision: 2))))
        XCTAssertThrowsError(try MemoryPolicyChangeReview(MemoryIntelligenceFixture.object(value, replacing: ["review_digest": .string(String(repeating: "0", count: 64))])))
        XCTAssertThrowsError(try MemoryPolicyChangeReview(MemoryIntelligenceFixture.object(value, replacing: ["expires_in_seconds": .number(121)])))
    }
    func testReviewAcceptsFractionalRemainingTTLAndRejectsZeroAndNonfiniteTime() throws {
        let policy = try MemoryIntelligenceFixture.policy()
        let value = try MemoryIntelligenceFixture.review(policy: policy, settings: MemoryIntelligenceSettings().value)
        let now = Date(timeIntervalSince1970: 1_000)
        let parsed = try MemoryPolicyChangeReview(MemoryIntelligenceFixture.object(value,
            replacing: ["expires_in_seconds": .number(119.75)]), now: now)
        XCTAssertEqual(parsed.expiresAt.timeIntervalSince(now), 119.75, accuracy: 0.000_001)
        for seconds in [Double.nan, .infinity, -.infinity, 0, -1, 120.01] {
            XCTAssertThrowsError(try MemoryPolicyChangeReview(MemoryIntelligenceFixture.object(value,
                replacing: ["expires_in_seconds": .number(seconds)]), now: now))
        }
    }
    @MainActor func testUnopenedMemoryMetadataDoesNotTreatZeroPlaceholdersAsARebuildSnapshot() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), store = MemoryIntelligenceStore(runtime: ProfileRuntimeGuard())
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await wire.setNotOpened(); await store.refresh()
        XCTAssertEqual(store.status?.index.state, "not_opened"); XCTAssertFalse(store.status?.index.isOpened ?? true)
        store.requestIndexRebuild(); await store.rebuildIndex(); await store.verifyIndex()
        XCTAssertNil(store.confirmation); XCTAssertNil(store.verification)
        let builds = await wire.count("index.rebuild"), verifies = await wire.count("index.verify")
        XCTAssertEqual(builds, 0); XCTAssertEqual(verifies, 0)
    }
    func testJobBoundsAndUnknownPhaseNeverLookSettled() throws {
        let job = MemoryIntelligenceFixture.job(id: String(repeating: "1", count: 32))
        XCTAssertFalse(try MemoryIntelligenceJob(job).settled)
        for changes: [String: RPCValue] in [["phase": .string("success_eventually")], ["policy_revision": .number(0)],
            ["progress": .object(["documents_done": .number(3), "documents_total": .number(2), "chunks": .number(0)])]] {
            XCTAssertThrowsError(try MemoryIntelligenceJob(MemoryIntelligenceFixture.object(job, replacing: changes)))
        }
    }
    func testLastRecallDropsExtraContentInsteadOfRetainingCorpus() throws {
        let row = RPCValue.object(["status": .string("ready"), "mode": .string("hybrid_local"),
            "citations": .number(2), "query_projected": .bool(true), "error": .null, "run_id": .null,
            "unexpected_note": .string("OWN SYNTHETIC TEXT NOT RETAINED")])
        let summary = try MemoryRecallSummary(row)
        XCTAssertEqual(summary.citations, 2); XCTAssertTrue(summary.queryProjected)
        XCTAssertEqual(Mirror(reflecting: summary).children.count, 6)
    }
    @MainActor func testOpeningPanelOnlyReadsAndModelNeedsExplicitConfirmation() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard()
        let store = MemoryIntelligenceStore(runtime: runtime)
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await store.refresh(); await store.prepareModel()
        var prepares = await wire.count("model.prepare"); XCTAssertEqual(prepares, 0)
        store.requestModelPreparation(); XCTAssertNotNil(store.confirmation)
        await store.prepareModel()
        prepares = await wire.count("model.prepare"); XCTAssertEqual(prepares, 1)
        XCTAssertEqual(store.job?.phase, .running); XCTAssertTrue(store.blocksInteraction)
    }
    @MainActor func testLostPrepareAcknowledgementIsReconciledByExactIDWithoutReplay() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), store = MemoryIntelligenceStore(runtime: ProfileRuntimeGuard())
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await store.refresh(); await wire.setLosePrepare(); store.requestModelPreparation(); await store.prepareModel()
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertFalse(store.mayStartJob)
        await store.prepareModel(); await store.refresh()
        XCTAssertFalse(store.responseUnconfirmed)
        let prepare = await wire.params("model.prepare"), get = await wire.params("job.get")
        XCTAssertEqual(prepare?["request_id"], get?["job_id"])
        let count = await wire.count("model.prepare"); XCTAssertEqual(count, 1)
    }
    @MainActor func testLostPolicyAcknowledgementUsesCASObservationWithoutReplay() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), store = MemoryIntelligenceStore(runtime: ProfileRuntimeGuard())
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await store.refresh(); store.draft = .init(autoRecallEnabled: true, mode: .hybridLocal)
        await store.reviewPolicy(); await wire.setLoseApply(); await store.applyPolicy()
        XCTAssertTrue(store.responseUnconfirmed); await store.applyPolicy(); await store.refresh()
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertEqual(store.status?.policy.revision, 2)
        XCTAssertTrue(store.status?.policy.settings.autoRecallEnabled == true)
        let count = await wire.count("policy.apply"); XCTAssertEqual(count, 1)
    }
    @MainActor func testRebuildPinsGenerationPolicyAndModelAndCancelRetainsCleanupOwner() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), store = MemoryIntelligenceStore(runtime: ProfileRuntimeGuard())
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await store.refresh(); store.requestIndexRebuild(); await store.rebuildIndex(); await store.cancelJob()
        XCTAssertEqual(store.job?.phase, .cleanupPending); XCTAssertFalse(store.mayStartJob)
        XCTAssertFalse(store.mayCancel); XCTAssertTrue(store.blocksInteraction)
        let rebuilds = await wire.count("index.rebuild"); XCTAssertEqual(rebuilds, 1)
    }
    @MainActor func testReadFromPreviousWorkspaceEpochCannotPopulateNewPanel() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), barrier = MemoryIntelligenceBarrier()
        let store = MemoryIntelligenceStore(runtime: runtime)
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "first-workspace")
        await wire.setBarrier(barrier)
        let read = Task { await store.refresh() }
        await barrier.waitForEntry()
        store.select(scope: MemoryIntelligenceFixture.other, contextKey: "second-workspace")
        await barrier.resume(); await read.value
        XCTAssertNil(store.status); XCTAssertFalse(store.isRefreshing)
        XCTAssertEqual(store.scope, MemoryIntelligenceFixture.other)
    }
    func testRetiredRuntimeRejectsResultAfterAwait() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), barrier = MemoryIntelligenceBarrier()
        let client = MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) })
        await wire.setBarrier(barrier)
        let read = Task { try await client.status(scope: MemoryIntelligenceFixture.scope) }
        await barrier.waitForEntry(); runtime.retire(); await barrier.resume()
        do { _ = try await read.value; XCTFail("Un runtime retirado no puede entregar un estado nuevo.") }
        catch { XCTAssertTrue(error is CancellationError) }
    }
    @MainActor func testHidingPanelDiscardsUnsentReviewButDoesNotCancelRealJob() async throws {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), store = MemoryIntelligenceStore(runtime: ProfileRuntimeGuard())
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await store.refresh(); store.requestModelPreparation(); await store.prepareModel()
        store.setVisible(UUID(), visible: false)
        XCTAssertEqual(store.job?.phase, .running)
        let cancels = await wire.count("job.cancel"); XCTAssertEqual(cancels, 0)
    }
    @MainActor func testContextGuardRejectsEffectBeforeSwiftUIProcessesContextChange() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard()
        let store = MemoryIntelligenceStore(runtime: runtime)
        var current = true
        store.isContextCurrent = { _, _ in current }
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "first-workspace")
        await store.refresh(); store.requestModelPreparation(); current = false
        await store.prepareModel()
        let prepares = await wire.count("model.prepare"); XCTAssertEqual(prepares, 0)
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertFalse(store.mayReview)
    }
    @MainActor func testContextChangeDuringMonitorSettlementDoesNotSubmitOrInventUncertainty() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard(), barrier = MemoryIntelligenceBarrier()
        let store = MemoryIntelligenceStore(runtime: runtime)
        var current = true
        store.isContextCurrent = { _, _ in current }
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "first-workspace")
        await store.refresh(); store.requestModelPreparation()
        store.waitForSupervisionIdle = { await barrier.suspend() }
        let operation = Task { await store.prepareModel() }
        await barrier.waitForEntry(); current = false; await barrier.resume(); await operation.value
        let prepares = await wire.count("model.prepare"); XCTAssertEqual(prepares, 0)
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertFalse(store.blocksInteraction)
    }
    @MainActor func testRunningAndCleanupPendingJobDoNotDisableRevocation() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard()
        let store = MemoryIntelligenceStore(runtime: runtime)
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await store.refresh(); store.draft = .init(autoRecallEnabled: true, mode: .hybridLocal, autoIndexEnabled: true)
        await store.reviewPolicy(); await store.applyPolicy()
        store.requestModelPreparation(); await store.prepareModel()
        XCTAssertEqual(store.job?.phase, .running); XCTAssertTrue(store.mayReview)
        await store.revokeAutomation()
        XCTAssertEqual(store.review?.settings, .init(mode: .hybridLocal))
        store.discardReview(); await store.cancelJob()
        XCTAssertEqual(store.job?.phase, .cleanupPending); XCTAssertTrue(store.mayReview)
        await store.revokeAutomation(); XCTAssertTrue(store.mayApply)
        XCTAssertFalse(store.review?.settings.autoRecallEnabled ?? true)
    }
    func testPreviousCopyIsOptionalMetadataWithoutPathsAndHasFiniteCapacity() throws {
        let legacy = try MemoryIntelligenceStatus(MemoryIntelligenceFixture.status(), expectedScope: MemoryIntelligenceFixture.scope)
        XCTAssertNil(legacy.model.previousCopy)
        let copy = RPCValue.object(["id": .string(String(repeating: "9", count: 32)), "bytes": .number(1024)])
        let current = try MemoryIntelligenceStatus(MemoryIntelligenceFixture.status(previous: copy), expectedScope: MemoryIntelligenceFixture.scope)
        XCTAssertEqual(current.model.previousCopy?.bytes, 1024)
        for changes: [String: RPCValue] in [["path": .string("/synthetic/never-read")], ["id": .string("unsafe")], ["bytes": .number(256_000_001)]] {
            XCTAssertThrowsError(try MemoryIntelligenceStatus(MemoryIntelligenceFixture.status(previous:
                MemoryIntelligenceFixture.object(copy, replacing: changes)), expectedScope: MemoryIntelligenceFixture.scope))
        }
    }
    @MainActor func testRetirePreviousCopyRequiresExplicitConfirmationAndReconcilesLostACK() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard()
        let store = MemoryIntelligenceStore(runtime: runtime), copyID = String(repeating: "9", count: 32)
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await wire.setPrevious(copyID); await store.refresh(); await store.retirePreviousModelCopy()
        var calls = await wire.count("model.retire"); XCTAssertEqual(calls, 0)
        store.requestPreviousModelCopyRetirement(); XCTAssertEqual(store.confirmation?.previousCopy?.id, copyID)
        await wire.setLoseRetire(); await store.retirePreviousModelCopy()
        XCTAssertTrue(store.responseUnconfirmed); await store.retirePreviousModelCopy(); await store.refresh()
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertEqual(store.job?.kind, .modelRetire)
        let submitted = await wire.params("model.retire"), fetched = await wire.params("job.get")
        XCTAssertEqual(submitted?["copy_id"], .string(copyID)); XCTAssertEqual(submitted?["request_id"], fetched?["job_id"])
        XCTAssertEqual(Set(submitted?.keys.map { $0 } ?? []), ["session_id", "temporary", "request_id", "copy_id"])
        calls = await wire.count("model.retire"); XCTAssertEqual(calls, 1)
    }
    @MainActor func testReplacingPreviousCopyInvalidatesUnsentRetirement() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard()
        let store = MemoryIntelligenceStore(runtime: runtime)
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: MemoryIntelligenceFixture.scope, contextKey: "synthetic-workspace")
        await wire.setPrevious(String(repeating: "9", count: 32)); await store.refresh(); store.requestPreviousModelCopyRetirement()
        XCTAssertNotNil(store.confirmation)
        await wire.setPrevious(String(repeating: "8", count: 32)); await store.refresh(); await store.retirePreviousModelCopy()
        XCTAssertNil(store.confirmation)
        let calls = await wire.count("model.retire"); XCTAssertEqual(calls, 0)
    }
    func testRecallNoticeBelongsToExactRunAndDisabledDefaultHasNoBanner() throws {
        let run = String(repeating: "7", count: 32)
        let base = RPCValue.object(["status": .string("unavailable"), "mode": .string("hybrid_local"),
            "citations": .number(0), "query_projected": .bool(false), "error": .string("missing_model"), "run_id": .string(run)])
        XCTAssertNotNil(try MemoryRecallSummary(base).notice(for: run))
        XCTAssertNil(try MemoryRecallSummary(base).notice(for: String(repeating: "6", count: 32)))
        XCTAssertNil(try MemoryRecallSummary(MemoryIntelligenceFixture.object(base, replacing:
            ["status": .string("disabled"), "query_projected": .bool(true)])).notice(for: run))
        let projected = try MemoryRecallSummary(MemoryIntelligenceFixture.object(base, replacing:
            ["status": .string("ready"), "query_projected": .bool(true), "error": .null]))
        XCTAssertTrue(projected.notice(for: run)?.contains("consulta acotada") == true)
    }
    @MainActor func testTemporaryChatCannotPrepareOrRetireDurableModelCopies() async {
        let wire = MemoryIntelligenceWire(), runtime = ProfileRuntimeGuard()
        let store = MemoryIntelligenceStore(runtime: runtime)
        store.configure(MemoryIntelligenceClient(runtime: runtime, request: { try await wire.request($0, $1) }))
        store.select(scope: SessionScope(sessionID: MemoryIntelligenceFixture.scope.sessionID, temporary: true), contextKey: "temporary")
        await wire.setPrevious(String(repeating: "9", count: 32)); await store.refresh()
        store.requestModelPreparation(); await store.prepareModel()
        store.requestPreviousModelCopyRetirement(); await store.retirePreviousModelCopy()
        XCTAssertNil(store.confirmation)
        let preparations = await wire.count("model.prepare"), retirements = await wire.count("model.retire")
        XCTAssertEqual(preparations, 0); XCTAssertEqual(retirements, 0)
    }
}
