import Foundation
import Observation
import XCTest
@testable import DesktopCore

enum RecoveryFixture {
    static let reviewID = String(repeating: "a", count: 32), operationID = String(repeating: "b", count: 32)
    static let hash = String(repeating: "c", count: 64)
    static let identity: RPCValue = .object(["device": .string("1"), "inode": .string("42")])
    static var scope: RPCValue { .object(["profile_id": .string("synthetic_recovery"), "generation": .number(7)]) }
    static var entry: RPCValue { .object(["path": .string("selected.txt"), "role": .string("workspace_file"), "kind": .string("file")]) }
    static func review(_ changes: [String: RPCValue] = [:]) throws -> RPCValue {
        var fields: [String: RPCValue] = ["kind": .string("workspace.backup.create"), "profile_id": .string("synthetic_recovery"), "generation": .number(7), "source_path": .string("/private/tmp/fixture-workspace"), "source_identity": identity, "destination_parent": .string("/private/tmp/fixture-parent"), "destination_name": .string("portable"), "destination_identity": identity, "summary": .object(["manifest_sha256": .string(hash), "file_count": .number(1), "directory_count": .number(0), "artifact_count": .number(0), "total_bytes": .number(7)]), "selection": .array([entry]), "semantics": .string("selected_paths_only_no_directory_recursion"), "credentials_included": .bool(false), "activation_included": .bool(false), "authenticity": .string("not_established"), "content_warning": .string("selected_content_may_contain_secrets")]
        fields.merge(changes) { _, new in new }; return try wrap(.object(fields))
    }
    static func wrap(_ metadata: RPCValue, ttl: Int = 120) throws -> RPCValue { .object(["review_id": .string(reviewID), "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata))), "expires_in_seconds": .number(Double(ttl)), "metadata": metadata]) }
    static func operation(kind: String = "workspace.backup.create", phase: String = "awaiting_owner", review: RPCValue? = nil, cleanup: Bool = false, receipt: RPCValue = .null, durable: Bool = false) throws -> RPCValue {
        let review = try review ?? Self.review(), effect = RecoveryExtensionValidation.effects.contains(kind)
        return .object(["operation_id": .string(effect ? operationID : String(repeating: "d", count: 32)), "kind": .string(kind), "phase": .string(phase), "stage": .null, "result": kind.hasSuffix(".review") && phase == "complete" ? .object(["review": review]) : .null, "error_code": .null, "receipt": receipt, "receipt_durable": .bool(durable), "worker_pending": .bool(phase == "working"), "cancellation_requested": .bool(false), "scope": scope, "review_id": effect ? review["review_id"] : .null, "review_digest": effect ? review["review_digest"] : .null, "cleanup_owner_pending": .bool(cleanup)])
    }
    static func approval(_ changes: [String: RPCValue] = [:]) throws -> RPCValue {
        let review = try Self.review(), args = try MaintenanceValidation.canonical(.object(["review_id": review["review_id"], "review_digest": review["review_digest"], "review": review["metadata"]]))
        let binding: RPCValue = .object(["domain": .string("jarvis.host.approval/1"), "profile": scope["profile_id"], "generation": .number(7), "purpose": .string("workspace.backup.create"), "operation": .string(operationID), "arguments_json": .string(args)])
        var row: [String: RPCValue] = ["approval_id": .string(String(repeating: "e", count: 32)), "scope": scope, "purpose": .string("workspace.backup.create"), "operation_id": .string(operationID), "arguments_json": .string(args), "digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(binding))), "expires_at_ns": .string("4102444800000000000")]
        row.merge(changes) { _, new in new }; return .object(row)
    }
}

private actor RecoveryWire {
    var calls: [String] = [], rows: [RPCValue] = []
    var loseSubmit = false, loseRead = false, ticket = false
    var ticketOnSubmit = false
    var decisions: [[String: RPCValue]] = []
    var gate: RecoveryGate?
    func configure(loseSubmit: Bool = false, loseRead: Bool = false) { self.loseSubmit = loseSubmit; self.loseRead = loseRead }
    func setRows(_ rows: [RPCValue], ticket: Bool = false) { self.rows = rows; self.ticket = ticket }
    func setGate(_ gate: RecoveryGate?) { self.gate = gate }
    func publishTicketOnSubmit() { ticketOnSubmit = true }
    func recordedDecisions() -> [[String: RPCValue]] { decisions }
    func count(_ method: String) -> Int { calls.filter { $0 == method }.count }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        calls.append(method)
        switch method {
        case "maintenance.status":
            if let gate { await gate.wait() }
            if let id = params["operation_id"], let row = rows.first(where: { $0["operation_id"] == id }) { return row }
            return .object(["profile_id": RecoveryFixture.scope["profile_id"], "generation": .number(7), "closing": .bool(false), "operations": .array(rows), "previous_operations": .array([])])
        case "maintenance.pending": return .object(["profile_id": RecoveryFixture.scope["profile_id"], "generation": .number(7), "pending": .array(ticket ? [try RecoveryFixture.approval()] : [])])
        case "workspace.backup.review":
            if loseRead { throw RPCFailure.disconnected }
            return try RecoveryFixture.operation(kind: method, phase: "complete")
        case "workspace.backup.create":
            rows = [try RecoveryFixture.operation()]
            ticket = ticketOnSubmit
            if loseSubmit { throw RPCFailure.disconnected }; return rows[0]
        case "maintenance.decide": decisions.append(params); return .object(["accepted": .bool(true)])
        case "maintenance.cleanup":
            let old = try XCTUnwrap(rows.first?.object); var row = old
            row["cleanup_owner_pending"] = .bool(false); row["phase"] = .string("failed"); rows = [.object(row)]; return rows[0]
        default: throw RPCFailure.configuration
        }
    }
}
private actor RecoveryGate {
    var released = false
    private var entered = false
    private let onEntry: @Sendable () -> Void
    var waiters: [CheckedContinuation<Void, Never>] = []
    init(onEntry: @escaping @Sendable () -> Void = {}) { self.onEntry = onEntry }
    func wait() async {
        if !entered { entered = true; onEntry() }
        if released { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func release() { released = true; let current = waiters; waiters.removeAll(); for waiter in current { waiter.resume() } }
}
private final class RecoveryTicketChanges: @unchecked Sendable {
    private let lock = NSLock()
    private var changes = 0
    func changed() { lock.lock(); changes += 1; lock.unlock() }
    var count: Int { lock.lock(); defer { lock.unlock() }; return changes }
}

final class RecoveryExtensionTests: XCTestCase {
    func testWorkspaceReviewRejectsSemanticDowngradeUnknownKeysAndDigestForgery() throws {
        let review = try RecoveryExtensionReview(RecoveryFixture.review())
        XCTAssertEqual(review.workspace?.summary.files, 1)
        XCTAssertEqual(review.workspace?.selection.first?.path, "selected.txt")
        let entry = try XCTUnwrap(review.workspace?.selection.first)
        XCTAssertNotEqual(entry.id, entry.reviewedID)
        for changes: [String: RPCValue] in [["credentials_included": .bool(true)], ["activation_included": .bool(true)], ["semantics": .string("recursive")], ["authenticity": .string("verified")], ["extra": .bool(true)]] {
            XCTAssertThrowsError(try RecoveryExtensionReview(RecoveryFixture.review(changes)))
        }
        var forged = try XCTUnwrap(RecoveryFixture.review().object); forged["review_digest"] = .string(RecoveryFixture.hash)
        XCTAssertThrowsError(try RecoveryExtensionReview(.object(forged)))
    }
    func testPortableSelectionPathsAndTicketMatchEveryBoundField() throws {
        for path in ["../outside", "/absolute", "a//b", "x\\y", "bad\u{202E}name", "cafe\u{301}"] { XCTAssertThrowsError(try RecoveryWorkspaceEntry(path: path)) }
        let review = try RecoveryExtensionReview(RecoveryFixture.review()), operation = try MaintenanceOperation(RecoveryFixture.operation())
        let ticket = try MaintenanceApproval(RecoveryFixture.approval())
        XCTAssertTrue(review.matches(ticket, operation: operation))
        let expired = try RecoveryExtensionReview(RecoveryFixture.wrap(review.metadata, ttl: 0))
        XCTAssertFalse(expired.matches(ticket, operation: operation))
        var bad = try XCTUnwrap(RecoveryFixture.operation().object); bad["review_id"] = .string(String(repeating: "f", count: 32))
        XCTAssertFalse(review.matches(ticket, operation: try MaintenanceOperation(.object(bad))))
    }
    @MainActor private func store(_ wire: RecoveryWire, runtime: ProfileRuntimeGuard = ProfileRuntimeGuard()) -> RecoveryExtensionStore {
        let store = RecoveryExtensionStore(runtime: runtime, maxPolls: 1, pause: { try await Task.sleep(for: .seconds(20)) })
        store.configure(RecoveryExtensionClient(runtime: runtime) { try await wire.request($0, $1) }); store.setVisible(UUID(), visible: true); return store
    }
    @MainActor private func prepare(_ store: RecoveryExtensionStore) async throws {
        await store.prepareWorkspaceBackup(entries: [try RecoveryWorkspaceEntry(path: "selected.txt")], parent: "/private/tmp/fixture-parent", name: "portable")
        XCTAssertNotNil(store.preview, store.errorMessage ?? "sin revisión")
    }
    @MainActor private func submitWithVisibleTicket(_ store: RecoveryExtensionStore,
                                                  wire: RecoveryWire) async throws -> MaintenanceApproval {
        // Publish a coherent host state before submit starts its background
        // reader. Await the observable publication, never an arbitrary number
        // of refresh/yield calls that can all skip the same in-flight read.
        await wire.publishTicketOnSubmit()
        await store.submit()
        if let approval = store.approval { return approval }
        // submit clears the old card before its RPC. Observe only after that
        // reset so nil-to-nil invalidation cannot satisfy this publication.
        let published = expectation(description: "Approval was published by the submitted operation")
        withObservationTracking { _ = store.approval } onChange: { published.fulfill() }
        await fulfillment(of: [published], timeout: 1)
        return try XCTUnwrap(store.approval, store.errorMessage ?? "no approval after publication")
    }
    @MainActor func testLostSubmissionACKReconcilesExactReviewWithoutResend() async throws {
        let wire = RecoveryWire(); await wire.configure(loseSubmit: true); let store = store(wire)
        try await prepare(store); await store.submit()
        XCTAssertTrue(store.responseUnconfirmed); XCTAssertTrue(store.blocksInteraction)
        await store.submit(); let submissions = await wire.count("workspace.backup.create"); XCTAssertEqual(submissions, 1)
        await store.refresh(); XCTAssertFalse(store.responseUnconfirmed); XCTAssertNotNil(store.operation)
        await store.close()
    }
    @MainActor func testCleanupOfReadWithoutReceiptKeepsCustodyThenOnlyReleasesOwner() async throws {
        let wire = RecoveryWire(); let pending = try RecoveryFixture.operation(kind: "workspace.backup.review", phase: "cleanup_pending", cleanup: true)
        await wire.setRows([pending]); let store = store(wire)
        await store.refresh(); XCTAssertTrue(store.blocksInteraction)
        // No local read ID may be guessed from unrelated status. Start a known
        // read through a transport that returns exactly that pending owner.
        let runtime = ProfileRuntimeGuard(), captured = RecoveryExtensionStore(runtime: runtime, maxPolls: 1, pause: {})
        captured.setVisible(UUID(), visible: true)
        captured.configure(RecoveryExtensionClient(runtime: runtime) { method, params in
            if method == "maintenance.status" && params.isEmpty { return .object(["profile_id": RecoveryFixture.scope["profile_id"], "generation": .number(7), "closing": .bool(false), "operations": .array([]), "previous_operations": .array([])]) }
            if method == "workspace.backup.review" { return pending }
            return try await wire.request(method, params)
        })
        await captured.prepareWorkspaceBackup(entries: [try RecoveryWorkspaceEntry(path: "selected.txt")], parent: "/private/tmp/fixture-parent", name: "portable")
        XCTAssertTrue(captured.readAwaitingSettlement); XCTAssertNotNil(captured.cleanupOperation)
        await captured.cleanup(); XCTAssertFalse(captured.readAwaitingSettlement)
        let cleanups = await wire.count("maintenance.cleanup"), submissions = await wire.count("workspace.backup.create"); XCTAssertEqual(cleanups, 1); XCTAssertEqual(submissions, 0)
        await captured.close(); await store.close()
    }
    @MainActor func testOwnerClickDuringStatusReadIsNotLostOrRebound() async throws {
        let wire = RecoveryWire(); let store = store(wire); try await prepare(store)
        let ticket = try await submitWithVisibleTicket(store, wire: wire); XCTAssertTrue(store.canDecide)
        let entered = expectation(description: "The overlapping status read is held before its response")
        let gate = RecoveryGate(onEntry: { entered.fulfill() }); await wire.setGate(gate)
        let refresh = Task { await store.refresh() }
        await fulfillment(of: [entered], timeout: 1)
        XCTAssertTrue(store.isRefreshing); XCTAssertTrue(store.canDecide)
        await store.decide(allow: false, ticket: ticket)
        let decisions = await wire.recordedDecisions()
        XCTAssertEqual(decisions, [ticket.params.merging(["allow": .bool(false)]) { _, new in new }])
        await gate.release(); await refresh.value; await wire.setGate(nil)
        XCTAssertNil(store.approval, "A pre-decision status response must not recreate the retired ticket")
        XCTAssertEqual(store.operation?.id, ticket.operationID)
        await store.close()
    }
    @MainActor func testIdenticalPollDoesNotWithdrawOrRecreateDisplayedTicket() async throws {
        let wire = RecoveryWire(), store = store(wire); try await prepare(store)
        let ticket = try await submitWithVisibleTicket(store, wire: wire), changes = RecoveryTicketChanges()
        withObservationTracking { _ = store.approval } onChange: { changes.changed() }
        await store.refresh()
        XCTAssertEqual(store.approval?.id, ticket.id); XCTAssertTrue(store.canDecide)
        XCTAssertEqual(changes.count, 0, "An identical status poll must not invalidate the displayed owner card")
        await store.close()
    }
    @MainActor func testRetiredRuntimeCannotSubmitOrDecide() async throws {
        let wire = RecoveryWire(), runtime = ProfileRuntimeGuard(); let store = store(wire, runtime: runtime)
        try await prepare(store); runtime.retire(); await store.submit()
        let submissions = await wire.count("workspace.backup.create"); XCTAssertEqual(submissions, 0); XCTAssertFalse(store.maySubmit)
        await store.close()
    }
    @MainActor func testLostReadStartACKFindsUniqueNewOwnerAndCleansWithoutAdoptingReview() async throws {
        let wire = RecoveryWire(); await wire.configure(loseRead: true); let store = store(wire)
        await store.prepareWorkspaceBackup(entries: [try RecoveryWorkspaceEntry(path: "selected.txt")], parent: "/private/tmp/fixture-parent", name: "portable")
        XCTAssertTrue(store.readAwaitingSettlement); XCTAssertNil(store.preview); XCTAssertNil(store.readOperation)
        await wire.setRows([try RecoveryFixture.operation(kind: "workspace.backup.review", phase: "cleanup_pending", cleanup: true)])
        await store.refresh(); XCTAssertNotNil(store.cleanupOperation); XCTAssertNil(store.preview); XCTAssertNil(store.effectReview)
        await store.cleanup(); XCTAssertFalse(store.readAwaitingSettlement)
        let reads = await wire.count("workspace.backup.review"), effects = await wire.count("workspace.backup.create"), cleanups = await wire.count("maintenance.cleanup")
        XCTAssertEqual(reads, 1); XCTAssertEqual(effects, 0); XCTAssertEqual(cleanups, 1)
        await store.close()
    }
    @MainActor func testClosingRPCDoesNotPermitReplacingConnectionWithUnsettledEffect() async throws {
        let wire = RecoveryWire(), runtime = ProfileRuntimeGuard(); let store = store(wire, runtime: runtime)
        try await prepare(store); await store.submit(); let id = store.operation?.id
        await store.close()
        store.configure(RecoveryExtensionClient(runtime: runtime) { try await wire.request($0, $1) })
        XCTAssertEqual(store.operation?.id, id); XCTAssertTrue(store.blocksInteraction); XCTAssertNotNil(store.errorMessage)
    }
    func testReadCannotClaimReceiptOrDurabilityAndResultsCannotClaimActivation() throws {
        XCTAssertThrowsError(try RecoveryExtensionValidation.validateResult(kind: "workspace.backup.review", result: .object(["review": RecoveryFixture.review()]), phase: "complete", receiptDurable: true, hasReceipt: false))
        let result: RPCValue = .object(["workspace": .object(["target_identity": RecoveryFixture.identity, "manifest_sha256": .string(RecoveryFixture.hash), "file_count": .number(1), "directory_count": .number(0), "total_bytes": .number(7), "effect_status": .string("new_unselected_workspace"), "activation_required": .bool(false), "replay_allowed": .bool(true)]), "association_changed": .bool(true)])
        XCTAssertThrowsError(try RecoveryExtensionValidation.validateResult(kind: "workspace.restore.stage", result: result, phase: "complete", receiptDurable: true, hasReceipt: true))
    }
    func testProfilePinRequiresBoundedSortedSupportedMaintenancePurposes() throws {
        let supported = ["backup.create", "doctor.repair.apply", "memory.restore.copy_scope", "profile.restore.activate", "release.rollback.apply", "release.update.apply", "restore.stage", "workspace.backup.create", "workspace.restore.stage"]
        var pin: [String: RPCValue] = ["profile_id": RecoveryFixture.scope["profile_id"], "generation": .number(7), "root_identity": RecoveryFixture.identity, "source_fingerprint": .string(RecoveryFixture.hash), "schemas": .object(["state": .number(2), "memory": .number(2), "schedules": .number(1)]), "maintenance_purposes": .array(supported.map(RPCValue.string)), "backup_manifest_sha256": .string(RecoveryFixture.hash), "restored_profile_activation_requires_owner": .bool(true), "profile_data_modified": .bool(false), "automatic_replay": .bool(false)]
        XCTAssertEqual(try RecoveryReleaseDetails.profilePin(.object(pin)), try MaintenanceScope(RecoveryFixture.scope))
        for values: [RPCValue] in [
            [.string("provider.login"), .string("release.rollback.apply"), .string("release.update.apply")],
            [.string("release.update.apply"), .string("release.rollback.apply")],
            [.string("release.rollback.apply"), .string("release.rollback.apply"), .string("release.update.apply")],
            [.string("release.rollback.apply"), .string("release.update.apply"), .bool(true)]
        ] {
            pin["maintenance_purposes"] = .array(values)
            XCTAssertThrowsError(try RecoveryReleaseDetails.profilePin(.object(pin)))
        }
        pin.removeValue(forKey: "maintenance_purposes")
        XCTAssertThrowsError(try RecoveryReleaseDetails.profilePin(.object(pin)))
        pin["maintenance_purposes"] = .array([.string("backup.create"), .string("release.rollback.apply"), .string("release.update.apply")])
        XCTAssertNoThrow(try RecoveryReleaseDetails.profilePin(.object(pin)))
        for values in [[], ["backup.create"], ["release.update.apply"], ["release.rollback.apply"]] {
            pin["maintenance_purposes"] = .array(values.map(RPCValue.string))
            XCTAssertThrowsError(try RecoveryReleaseDetails.profilePin(.object(pin)))
        }
    }
    func testLaunchPlanCannotOfferRuntimeThatDropsRequiredMaintenancePurposes() throws {
        let purposes: RPCValue = .array([.string("release.rollback.apply"), .string("release.update.apply")])
        var pin: [String: RPCValue] = ["profile_id": RecoveryFixture.scope["profile_id"], "generation": .number(7), "root_identity": RecoveryFixture.identity, "source_fingerprint": .string(RecoveryFixture.hash), "schemas": .object(["state": .number(2), "memory": .number(2), "schedules": .number(1)]), "maintenance_purposes": purposes, "backup_manifest_sha256": .string(RecoveryFixture.hash), "restored_profile_activation_requires_owner": .bool(true), "profile_data_modified": .bool(false), "automatic_replay": .bool(false)]
        var row: [String: RPCValue] = ["install_root": .string("/private/tmp/installation"), "release_root": .string("/private/tmp/installation/releases/v1"), "version_key": .string("v1"), "manifest_sha256": .string(RecoveryFixture.hash), "current_sha256": .string(RecoveryFixture.hash), "native_executable": .string("/private/tmp/installation/releases/v1/app/Jarvis.app/Contents/MacOS/Jarvis"), "python_source": .string("/private/tmp/installation/releases/v1/python"), "profile_pin": .object(pin), "maintenance_purposes": purposes, "payload_verified": .bool(true), "same_release_source_and_native": .bool(true), "release_authenticity": .string("not_established"), "channel": .string("development"), "external_python_required": .bool(true), "external_official_codex_required": .bool(true), "launch_performed": .bool(false), "restored_profile_activation_requires_owner": .bool(true)]
        XCTAssertNoThrow(try RecoveryLaunchPlan(.object(["release_launch": .object(row)])))
        pin["maintenance_purposes"] = .array([.string("release.rollback.apply"), .string("release.update.apply"), .string("workspace.backup.create")])
        row["profile_pin"] = .object(pin)
        XCTAssertThrowsError(try RecoveryLaunchPlan(.object(["release_launch": .object(row)])))
        row["maintenance_purposes"] = pin["maintenance_purposes"]
        XCTAssertNoThrow(try RecoveryLaunchPlan(.object(["release_launch": .object(row)])))
        row.removeValue(forKey: "maintenance_purposes")
        XCTAssertThrowsError(try RecoveryLaunchPlan(.object(["release_launch": .object(row)])))
    }
}
