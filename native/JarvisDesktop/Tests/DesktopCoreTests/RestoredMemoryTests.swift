import Foundation
import XCTest
@testable import DesktopCore

enum RestoredMemoryFixture {
    static let reviewID = String(repeating: "a", count: 32)
    static let operationID = String(repeating: "b", count: 32)
    static let inventoryID = String(repeating: "c", count: 32)
    static let sourceID = String(repeating: "d", count: 32)
    static let hash = String(repeating: "e", count: 64)
    static let identity: RPCValue = .object(["device": .string("1"), "inode": .string("123")])
    static func scope(_ generation: Int = 7) -> RPCValue { .object(["profile_id": .string("synthetic_restored"), "generation": .number(Double(generation))]) }
    static func source(_ id: String = sourceID) -> RPCValue {
        .object(["source_handle": .string(id), "agent_id": .string("jarvis"), "source_generation": .string("4"), "documents": .number(2), "bytes": .number(40), "largest_document_bytes": .number(20), "fingerprint": .string(hash), "eligible": .bool(true), "reason": .null])
    }
    static func inventory(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        var row: [String: RPCValue] = ["inventory_id": .string(inventoryID), "expires_in_seconds": .number(120), "scope": scope(), "root_identity": identity, "agent_id": .string("jarvis"), "agent_revision": .string("9223372036854775807"), "schema_version": .number(1), "source_kind": .string("cold_delete_snapshot"), "destination": .object(["workspace_path": .string("/private/tmp/synthetic-workspace"), "workspace_identity": identity, "empty": .bool(true), "scope_present": .bool(false), "scope_generation": .null]), "sources": .array([source()]), "candidate_count": .number(1), "session_bound_scopes_excluded": .number(3), "next_cursor": .null, "page_size": .number(32), "provider_used": .bool(false), "source_workspace_resolved": .bool(false)]
        row.merge(changes) { _, new in new }; return .object(row)
    }
    static func review(_ changes: [String: RPCValue] = [:], ttl: Int = 120) throws -> RPCValue {
        let limits: [String: Int] = ["document_bytes":16_000,"scope_documents":1000,"scope_bytes":16_777_216,"memory_documents":10_000,"memory_scopes":10_000,"memory_text_bytes":67_108_864,"chunk_bytes":2048,"copy_receipts":256,"copy_mapping_rows":100_000,"copy_metadata_bytes":16_777_216,"copy_vm_steps":20_000_000,"memory_file_bytes":67_108_864,"profile_snapshot_bytes":134_217_728]
        var row: [String: RPCValue] = ["kind": .string("memory.restore.copy_scope"), "profile_id": .string("synthetic_restored"), "generation": .number(7), "root_identity": identity, "database_identity": identity, "agent_id": .string("jarvis"), "agent_revision": .string("9223372036854775807"), "inventory_id": .string(inventoryID), "source_handle": .string(sourceID), "source_generation": .string("4"), "source_fingerprint": .string(hash), "source_documents": .number(2), "source_bytes": .number(40), "largest_document_bytes": .number(20), "destination_workspace_path": .string("/private/tmp/synthetic-workspace"), "destination_workspace_identity": identity, "destination_scope_present": .bool(false), "destination_generation": .null, "destination_documents": .number(0), "core_review_digest": .string(hash), "schema_before": .number(1), "schema_after": .number(2), "migration_required": .bool(true), "limits": .object(limits.mapValues { .number(Double($0)) }), "policy": .string("same_agent_durable_scope_to_empty_current_workspace"), "retains_source": .bool(true), "new_document_ids": .bool(true), "new_document_revision": .number(1), "source_labels_verified": .bool(false), "uri_resources_opened": .bool(false), "provider_used": .bool(false), "grants_restored": .bool(false)]
        row.merge(changes) { _, new in new }; let metadata = RPCValue.object(row)
        return .object(["review_id": .string(reviewID), "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata))), "expires_in_seconds": .number(Double(ttl)), "metadata": metadata])
    }
    static func receipt(status: String = "completed", cleanup: Bool = false) throws -> RPCValue {
        let review = try RestoredMemoryReview(review())
        return .object(["operation_id": .string(operationID), "purpose": .string("memory.restore.copy_scope"), "request_digest": .string(review.digest), "core_review_digest": .string(hash), "status": .string(status), "effect_status": .string(status == "completed" ? "scope_copy_committed" : status == "unknown" ? "scope_copy_unknown" : "no_scope_copy_committed"), "document_count": .number(status == "completed" || status == "unknown" ? 2 : 0), "byte_length": .number(status == "completed" || status == "unknown" ? 40 : 0), "mapping_sha256": status == "completed" || status == "unknown" ? .string(hash) : .null, "destination_generation_after": status == "completed" || status == "unknown" ? .string("1") : .null, "error_code": status == "completed" ? .null : .string("memory_copy_cancelled"), "cleanup_pending": .bool(cleanup)])
    }
    static func operation(kind: String = "memory.restore.copy_scope", phase: String = "awaiting_owner", result: RPCValue = .null, receipt: RPCValue = .null, cleanup: Bool = false, durable: Bool = false) throws -> RPCValue {
        let effect = kind == "memory.restore.copy_scope", review = try RestoredMemoryReview(review())
        return .object(["operation_id": .string(effect ? operationID : String(repeating: "f", count: 32)), "kind": .string(kind), "phase": .string(phase), "stage": .null, "result": result, "error_code": .null, "receipt": receipt, "receipt_durable": .bool(durable), "worker_pending": .bool(phase == "working"), "cancellation_requested": .bool(false), "scope": scope(), "review_id": effect ? .string(review.id) : .null, "review_digest": effect ? .string(review.digest) : .null, "cleanup_owner_pending": .bool(cleanup)])
    }
    static func approval() throws -> RPCValue {
        let review = try RestoredMemoryReview(review())
        let args = try MaintenanceValidation.canonical(.object(["review_id": .string(review.id), "review_digest": .string(review.digest), "review": review.metadata]))
        let identity: RPCValue = .object(["domain": .string("jarvis.host.approval/1"), "profile": .string(review.scope.profileID), "generation": .number(7), "purpose": .string("memory.restore.copy_scope"), "operation": .string(operationID), "arguments_json": .string(args)])
        return .object(["approval_id": .string(String(repeating: "1", count: 32)), "scope": scope(), "purpose": .string("memory.restore.copy_scope"), "operation_id": .string(operationID), "arguments_json": .string(args), "digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(identity))), "expires_at_ns": .string("4102444800000000000")])
    }
    static func observation(match: Bool = true) throws -> RPCValue {
        let review = try RestoredMemoryReview(review())
        return .object(["observation": .object(["operation_id": .string(operationID), "request_digest": .string(review.digest), "origin_kind": .string("local"), "document_count": .number(2), "byte_length": .number(40), "mapping_sha256": .string(hash), "current_matching_documents": .number(match ? 2 : 1), "current_destination_generation": .string(match ? "1" : "2"), "current_documents_match": .bool(match), "current_scope_generation_matches": .bool(match), "state": .string("local_record_present"), "prior_acknowledgement": .string("not_established")]), "permits_replay": .bool(false)])
    }
}

private actor MemoryWire {
    var calls: [(String, [String: RPCValue])] = []
    var rows: [RPCValue] = []
    var hostGeneration = 7
    var inventoryValue = RestoredMemoryFixture.inventory()
    var pageValue: RPCValue?
    var loseInventoryACK = false
    var readWorking = false
    var reviewValue = try! RestoredMemoryFixture.review()
    var observed = RPCValue.object(["observation": .null, "permits_replay": .bool(false)])
    var loseSubmit = false
    var includeApproval = false
    var inventoryGate: MemoryGate?
    func setRows(_ rows: [RPCValue], generation: Int = 7) { self.rows = rows; hostGeneration = generation }
    func loseACK() { loseSubmit = true }
    func setReadMode(lose: Bool = false, working: Bool = false) { loseInventoryACK = lose; readWorking = working }
    func setPage(_ value: RPCValue) { pageValue = value }
    func setObserved(_ value: RPCValue) { observed = value }
    func setInventory(_ value: RPCValue) { inventoryValue = value }
    func setGate(_ value: MemoryGate) { inventoryGate = value }
    func allowTicket() { includeApproval = true }
    func count(_ name: String) -> Int { calls.filter { $0.0 == name }.count }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append((method, params))
        switch method {
        case "maintenance.status":
            if let id = params["operation_id"]?.string, let row = rows.first(where: { $0["operation_id"] == .string(id) }) { return row }
            return .object(["profile_id": .string("synthetic_restored"), "generation": .number(Double(hostGeneration)), "closing": .bool(false), "operations": .array(rows), "previous_operations": .array([]), "blockers": .array([])])
        case "maintenance.pending": return .object(["profile_id": .string("synthetic_restored"), "generation": .number(Double(hostGeneration)), "pending": .array(includeApproval && hostGeneration == 7 ? [try RestoredMemoryFixture.approval()] : [])])
        case "memory.restore.inventory":
            if let inventoryGate { await inventoryGate.wait() }
            if loseInventoryACK { throw RPCFailure.disconnected }
            if readWorking { let row = try RestoredMemoryFixture.operation(kind: method, phase: "working"); rows = [row]; return row }
            return try RestoredMemoryFixture.operation(kind: method, phase: "complete", result: .object(["inventory": params["cursor"] != nil ? (pageValue ?? inventoryValue) : inventoryValue]))
        case "memory.restore.review": return try RestoredMemoryFixture.operation(kind: method, phase: "complete", result: .object(["review": reviewValue]))
        case "memory.restore.copy_scope":
            rows = [try RestoredMemoryFixture.operation()]
            if loseSubmit { throw RPCFailure.disconnected }
            return rows[0]
        case "memory.restore.receipt": return try RestoredMemoryFixture.operation(kind: method, phase: "complete", result: observed)
        case "maintenance.decide": return .object(["accepted": .bool(true)])
        case "maintenance.cleanup":
            rows = [try RestoredMemoryFixture.operation(phase: "publication_unknown", receipt: RestoredMemoryFixture.receipt(status: "unknown", cleanup: true), cleanup: false, durable: true)]
            return rows[0]
        default: throw RPCFailure.invalidResponse
        }
    }
}
private actor MemoryGate {
    var waiters: [CheckedContinuation<Void, Never>] = []
    var opened = false
    func wait() async { if opened { return }; await withCheckedContinuation { waiters.append($0) } }
    func open() { opened = true; let held = waiters; waiters = []; for waiter in held { waiter.resume() } }
}

final class RestoredMemoryTests: XCTestCase {
    func testStrictMetadataRejectsBodiesAgentsFormatsPrecisionAndAuthorityClaims() throws {
        _ = try RestoredMemoryInventory(RestoredMemoryFixture.inventory())
        for change: [String: RPCValue] in [["text": .string("SECRET")], ["provider_used": .bool(true)], ["source_workspace_resolved": .bool(true)], ["agent_revision": .number(7)], ["agent_revision": .string("01")], ["schema_version": .number(3)], ["sources": .array([RestoredMemoryFixture.source(), RestoredMemoryFixture.source()])]] {
            XCTAssertThrowsError(try RestoredMemoryInventory(RestoredMemoryFixture.inventory(change)))
        }
        for change: [String: RPCValue] in [["provider_used": .bool(true)], ["destination_documents": .number(1)], ["migration_required": .bool(false)], ["source_generation": .string("4503599627370496")], ["agent_revision": .string("9223372036854775808")], ["source_uri": .string("file:///private")]] {
            XCTAssertThrowsError(try RestoredMemoryReview(RestoredMemoryFixture.review(change)))
        }
        let review = try RestoredMemoryReview(RestoredMemoryFixture.review(ttl: 0)); XCTAssertLessThanOrEqual(review.expiresAt, Date())
    }
    func testTombstoneGenerationZeroIsDistinctFromAbsentAndCanonicalStringsStayExact() throws {
        var destination = RestoredMemoryFixture.inventory()["destination"].object!
        destination["scope_present"] = .bool(true); destination["scope_generation"] = .string("0")
        let value = try RestoredMemoryInventory(RestoredMemoryFixture.inventory(["destination": .object(destination)]))
        XCTAssertEqual(value.destinationGeneration, "0"); XCTAssertTrue(value.destinationPresent)
        destination["scope_present"] = .bool(false)
        XCTAssertThrowsError(try RestoredMemoryInventory(RestoredMemoryFixture.inventory(["destination": .object(destination)])))
        XCTAssertEqual(value.agentRevision, "9223372036854775807")
    }
    func testNewReceiptCannotMasqueradeAsRecoveryOrActivationAndCannotGrantReplay() throws {
        let row = try MaintenanceOperation(RestoredMemoryFixture.operation(phase: "complete", receipt: RestoredMemoryFixture.receipt(), durable: true))
        XCTAssertTrue(row.success); XCTAssertNotNil(row.memoryCopyReceipt); XCTAssertNil(row.activationReceipt); XCTAssertNil(row.receipt)
        XCTAssertThrowsError(try MaintenanceOperation(RestoredMemoryFixture.operation(phase: "complete", receipt: RestoredMemoryFixture.receipt(), durable: false)))
        var observed = try RestoredMemoryFixture.observation().object!; observed["permits_replay"] = .bool(true)
        XCTAssertThrowsError(try MemoryCopyObservation(.object(observed)))
        var wrong = try RestoredMemoryFixture.receipt().object!; wrong["core_review_digest"] = .string(String(repeating: "9", count: 64))
        let mismatch = try MaintenanceOperation(RestoredMemoryFixture.operation(phase: "complete", receipt: .object(wrong), durable: true))
        XCTAssertFalse(try RestoredMemoryReview(RestoredMemoryFixture.review()).matches(mismatch))
    }
    func testStatusParserRejectsUnprojectedReadResultsBeforeRetention() throws {
        var inventory = RestoredMemoryFixture.inventory().object!; inventory["body"] = .string("SECRET")
        XCTAssertThrowsError(try MaintenanceOperation(RestoredMemoryFixture.operation(kind: "memory.restore.inventory", phase: "complete", result: .object(["inventory": .object(inventory)]))))
        XCTAssertThrowsError(try MaintenanceOperation(RestoredMemoryFixture.operation(phase: "awaiting_owner", result: .object(["text": .string("SECRET")]))))
    }
    private func checkCount(_ wire: MemoryWire, _ name: String, _ expected: Int, file: StaticString = #filePath, line: UInt = #line) async { let count = await wire.count(name); XCTAssertEqual(count, expected, file: file, line: line) }
    @MainActor private func configured(_ wire: MemoryWire) -> (RestoredMemoryStore, ProfileRuntimeGuard, UUID) {
        let runtime = ProfileRuntimeGuard(), store = RestoredMemoryStore(runtime: runtime, maxPolls: 2, pause: { try await Task.sleep(for: .seconds(60)) })
        store.configure(RestoredMemoryClient(runtime: runtime) { try await wire.request($0, $1) }); let owner = UUID(); store.setVisible(owner, visible: true); store.selectAgent("jarvis")
        return (store, runtime, owner)
    }
    @MainActor private func reviewed(_ wire: MemoryWire) async throws -> (RestoredMemoryStore, ProfileRuntimeGuard, UUID) {
        let result = configured(wire); await result.0.loadInventory(); XCTAssertEqual(result.0.sources.count, 1)
        result.0.selectSource(RestoredMemoryFixture.sourceID); await result.0.prepareReview(); XCTAssertNotNil(result.0.preview); return result
    }
    @MainActor func testOwnerIsExplicitAndBoundToExactEffectWithNoProviderCalls() async throws {
        let wire = MemoryWire(), (store, _, _) = try await reviewed(wire)
        await checkCount(wire, "maintenance.decide", 0)
        await store.submit(); await wire.allowTicket(); await store.refresh()
        XCTAssertNotNil(store.approval); XCTAssertTrue(store.blocksInteraction)
        await store.decide(allow: false)
        await checkCount(wire, "maintenance.decide", 1)
        await checkCount(wire, "memory.restore.copy_scope", 1)
        await checkCount(wire, "service.status", 0)
        await store.close()
    }
    @MainActor func testClosingPreviewReleasesMonitorAndNeverSubmits() async throws {
        let wire = MemoryWire(), (store, _, owner) = try await reviewed(wire)
        XCTAssertTrue(store.blocksInteraction); store.setVisible(owner, visible: false)
        XCTAssertNil(store.preview); XCTAssertTrue(store.sources.isEmpty); XCTAssertFalse(store.monitorPaused)
        await checkCount(wire, "memory.restore.copy_scope", 0); await store.close()
    }
    @MainActor func testHiddenInventoryCompletionIsSettledThenDiscardedAndCannotRevivePreview() async throws {
        let wire = MemoryWire(), gate = MemoryGate(), (store, _, owner) = configured(wire)
        await wire.setGate(gate)
        let request = Task { await store.loadInventory() }
        for _ in 0..<100 { if await wire.count("memory.restore.inventory") > 0 { break }; await Task.yield() }
        store.setVisible(owner, visible: false); XCTAssertTrue(store.blocksInteraction)
        await gate.open(); await request.value
        XCTAssertNil(store.inventory); XCTAssertTrue(store.sources.isEmpty); XCTAssertFalse(store.blocksInteraction)
        await store.close()
    }
    @MainActor func testRetiredRuntimeDropsLateReplyAndCannotStartAnyNewRPC() async throws {
        let wire = MemoryWire(), gate = MemoryGate(), (store, runtime, _) = configured(wire)
        await wire.setGate(gate); let task = Task { await store.loadInventory() }
        for _ in 0..<100 { if await wire.count("memory.restore.inventory") > 0 { break }; await Task.yield() }
        runtime.retire(); await gate.open(); await task.value
        XCTAssertNil(store.inventory); await store.loadInventory()
        await checkCount(wire, "memory.restore.inventory", 1); await store.close()
    }
    @MainActor func testLostSubmitACKReconcilesTerminalExactReviewDespiteGenerationAdvanceNoResend() async throws {
        let wire = MemoryWire(), (store, _, owner) = try await reviewed(wire)
        await wire.loseACK(); await store.submit(); XCTAssertTrue(store.responseUnconfirmed)
        store.setVisible(owner, visible: false); XCTAssertNotNil(store.effectReview)
        await wire.setRows([try RestoredMemoryFixture.operation(phase: "complete", receipt: RestoredMemoryFixture.receipt(), durable: true)], generation: 8)
        await store.refresh()
        XCTAssertFalse(store.responseUnconfirmed); XCTAssertTrue(store.operation?.success == true); XCTAssertEqual(store.effectReview?.scope.generation, 7)
        XCTAssertFalse(store.blocksInteraction); await checkCount(wire, "memory.restore.copy_scope", 1)
        XCTAssertNil(store.approval); await store.close()
    }
    @MainActor func testGenerationAdvanceKeepsUnknownReceiptSeparateFromSQLObservationAndCleanupState() async throws {
        let wire = MemoryWire(), (store, _, owner) = try await reviewed(wire)
        await store.submit()
        await wire.setRows([try RestoredMemoryFixture.operation(phase: "cleanup_pending", receipt: RestoredMemoryFixture.receipt(status: "unknown", cleanup: true), cleanup: true, durable: true)], generation: 8)
        await store.refresh(); XCTAssertTrue(store.blocksInteraction); XCTAssertFalse(store.hasObservationTarget)
        store.setVisible(owner, visible: false); await store.cleanup()
        XCTAssertTrue(store.operation?.memoryCopyReceipt?.cleanupPending == true); XCTAssertFalse(store.operation?.cleanupOwnerPending == true)
        XCTAssertTrue(store.hasObservationTarget)
        await wire.setObserved(try RestoredMemoryFixture.observation(match: false)); await store.queryReceipt()
        XCTAssertTrue(store.observationWasQueried); XCTAssertEqual(store.observation?.documentsMatch, false)
        XCTAssertEqual(store.operation?.memoryCopyReceipt?.status, "unknown"); XCTAssertFalse(store.operation?.success == true)
        await checkCount(wire, "memory.restore.copy_scope", 1); await store.close()
    }
    @MainActor func testNullObservationCannotRepeatCopyOrEstablishRollback() async throws {
        let wire = MemoryWire(), (store, _, _) = try await reviewed(wire)
        await store.submit(); await wire.setRows([try RestoredMemoryFixture.operation(phase: "publication_unknown", receipt: RestoredMemoryFixture.receipt(status: "unknown"), durable: true)], generation: 8)
        await store.refresh(); await store.queryReceipt(); XCTAssertTrue(store.observationWasQueried); XCTAssertNil(store.observation)
        XCTAssertEqual(store.operation?.memoryCopyReceipt?.status, "unknown"); await checkCount(wire, "memory.restore.copy_scope", 1); await store.close()
    }
    @MainActor func testAbsentUnconfirmedACKCanOnlyBeAcknowledgedLocallyWithoutAnotherRequest() async throws {
        let wire = MemoryWire(), (store, _, _) = try await reviewed(wire)
        await wire.loseACK(); await store.submit(); await wire.setRows([], generation: 8); await store.refresh()
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertNil(store.operation)
        store.acknowledgeUnconfirmedLocally(); XCTAssertFalse(store.responseUnconfirmed)
        XCTAssertNotNil(store.effectReview); await checkCount(wire, "memory.restore.copy_scope", 1); await store.close()
    }
    @MainActor func testPagingAcceptsBoundedPagesAndRejectsSnapshotDrift() async throws {
        let wire = MemoryWire(), (store, _, _) = configured(wire)
        let cursor = String(repeating: "2", count: 32)
        await wire.setInventory(RestoredMemoryFixture.inventory(["candidate_count": .number(2), "next_cursor": .string(cursor), "expires_in_seconds": .number(30)]))
        await store.loadInventory(); XCTAssertEqual(store.sources.count, 1)
        await wire.setPage(RestoredMemoryFixture.inventory(["candidate_count": .number(2), "sources": .array([RestoredMemoryFixture.source(String(repeating: "3", count: 32))]), "expires_in_seconds": .number(120)]))
        await store.loadMore(); XCTAssertEqual(store.sources.count, 2); XCTAssertTrue(store.inventoryIsCurrent)
        await store.close()
        let other = configured(wire).0
        await other.loadInventory()
        await wire.setPage(RestoredMemoryFixture.inventory(["candidate_count": .number(2), "agent_revision": .string("2"), "sources": .array([RestoredMemoryFixture.source(String(repeating: "3", count: 32))])]))
        await other.loadMore(); XCTAssertNil(other.inventory); XCTAssertNotNil(other.errorMessage)
        await checkCount(wire, "memory.restore.copy_scope", 0); await other.close()
    }
    @MainActor func testRepeatedHandleIsRejectedEvenWhenPageHeaderMatches() async throws {
        let wire = MemoryWire(), (store, _, _) = configured(wire)
        await wire.setInventory(RestoredMemoryFixture.inventory(["candidate_count": .number(2), "next_cursor": .string(String(repeating: "2", count: 32))]))
        await store.loadInventory(); await wire.setPage(RestoredMemoryFixture.inventory(["candidate_count": .number(2)]))
        await store.loadMore(); XCTAssertNil(store.inventory); XCTAssertTrue(store.sources.isEmpty)
        await store.close()
    }
    @MainActor func testExpiredInventoryCannotIssueReviewAndAgentSelectionNeverDefaults() async throws {
        let wire = MemoryWire(), runtime = ProfileRuntimeGuard(), store = RestoredMemoryStore(runtime: runtime)
        store.configure(RestoredMemoryClient(runtime: runtime) { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true)
        await store.loadInventory(); await checkCount(wire, "memory.restore.inventory", 0)
        store.selectAgent("jarvis"); await wire.setInventory(RestoredMemoryFixture.inventory(["expires_in_seconds": .number(0)]))
        await store.loadInventory(); store.selectSource(RestoredMemoryFixture.sourceID); await store.prepareReview()
        await checkCount(wire, "memory.restore.review", 0); XCTAssertNil(store.preview); await store.close()
    }
    @MainActor func testAcceptedReadRemainsOwnedWhenPollingBudgetEndsThenReleasesOnlyAfterStatusSettles() async throws {
        let wire = MemoryWire(), runtime = ProfileRuntimeGuard(), store = RestoredMemoryStore(runtime: runtime, maxPolls: 2, pause: {})
        store.configure(RestoredMemoryClient(runtime: runtime) { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true); store.selectAgent("jarvis")
        await wire.setReadMode(working: true); await store.loadInventory()
        XCTAssertTrue(store.readAwaitingSettlement); XCTAssertTrue(store.blocksInteraction); XCTAssertNotNil(store.readOperation)
        await store.loadInventory(); await checkCount(wire, "memory.restore.inventory", 1)
        await wire.setRows([try RestoredMemoryFixture.operation(kind: "memory.restore.inventory", phase: "complete", result: .object(["inventory": RestoredMemoryFixture.inventory()]))])
        await store.refresh(); XCTAssertFalse(store.readAwaitingSettlement); XCTAssertNil(store.readOperation); XCTAssertNil(store.inventory)
        await checkCount(wire, "memory.restore.inventory", 1); await store.close()
    }
    @MainActor func testLostReadACKDoesNotReplayAndStatusCanReleaseReadOnlyWait() async throws {
        let wire = MemoryWire(), (store, _, _) = configured(wire)
        await wire.setReadMode(lose: true); await store.loadInventory(); XCTAssertTrue(store.readAwaitingSettlement)
        await store.loadInventory(); await checkCount(wire, "memory.restore.inventory", 1)
        await store.refresh(); XCTAssertFalse(store.readAwaitingSettlement); XCTAssertNil(store.inventory)
        await checkCount(wire, "memory.restore.inventory", 1); await store.close()
    }
    @MainActor func testStaleGenerationCannotApproveAndInvalidatesOnlyPreview() async throws {
        let wire = MemoryWire(), (store, _, _) = try await reviewed(wire)
        await store.submit(); await wire.allowTicket(); await wire.setRows([try RestoredMemoryFixture.operation()], generation: 8)
        await store.refresh(); XCTAssertNotNil(store.effectReview); XCTAssertNotNil(store.operation); XCTAssertNil(store.approval)
        await store.decide(allow: true); await checkCount(wire, "maintenance.decide", 0)
        await store.close()
    }
    @MainActor func testOldConfigurationResponseCannotAffectReplacementStoreEpoch() async throws {
        let old = MemoryWire(), replacement = MemoryWire(), gate = MemoryGate(), (store, runtime, _) = configured(old)
        await old.setGate(gate); let task = Task { await store.loadInventory() }
        for _ in 0..<100 { if await old.count("memory.restore.inventory") > 0 { break }; await Task.yield() }
        store.configure(RestoredMemoryClient(runtime: runtime) { try await replacement.request($0, $1) })
        await gate.open(); await task.value
        XCTAssertNil(store.inventory); XCTAssertFalse(store.isRequesting); XCTAssertFalse(store.readAwaitingSettlement)
        await checkCount(replacement, "memory.restore.inventory", 0); await store.close()
    }

    @MainActor func testClosingPendingCopyKeepsInteractionAndMonitorGatesUntilActualSettlement() async throws {
        let wire = MemoryWire(), (store, _, owner) = try await reviewed(wire)
        await store.submit(); store.setVisible(owner, visible: false)
        XCTAssertNil(store.preview); XCTAssertNotNil(store.effectReview)
        XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.monitorPaused); XCTAssertFalse(store.mayPrepare)
        store.selectAgent("other"); XCTAssertEqual(store.agentID,"jarvis")
        await store.loadInventory(); await checkCount(wire,"memory.restore.inventory",1)
        await wire.setRows([try RestoredMemoryFixture.operation(phase:"cleanup_pending",receipt:RestoredMemoryFixture.receipt(status:"unknown",cleanup:true),cleanup:true,durable:true)],generation:8)
        await store.refresh(); XCTAssertTrue(store.blocksInteraction); XCTAssertTrue(store.monitorPaused)
        XCTAssertFalse(store.mayPrepare); XCTAssertFalse(store.hasObservationTarget)
        await store.cleanup()
        XCTAssertFalse(store.blocksInteraction); XCTAssertFalse(store.monitorPaused)
        XCTAssertEqual(store.operation?.memoryCopyReceipt?.status,"unknown")
        XCTAssertTrue(store.operation?.memoryCopyReceipt?.cleanupPending == true)
        XCTAssertFalse(store.operation?.cleanupOwnerPending == true)
        await checkCount(wire,"memory.restore.copy_scope",1); await store.close()
    }

}
