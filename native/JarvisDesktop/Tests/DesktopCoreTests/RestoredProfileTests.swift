import Foundation
import XCTest
@testable import DesktopCore

enum RestoreFixture {
    static let operationID = String(repeating: "a", count: 32)
    static let reviewID = String(repeating: "b", count: 32)
    static let marker = String(repeating: "c", count: 64)
    static let admission = String(repeating: "d", count: 64)
    static let identity: RPCValue = .object(["device": .string("1"), "inode": .string("222")])
    static let scope: RPCValue = .object(["profile_id": .string("source_scope"), "generation": .number(2)])
    static func reviewValue() throws -> RPCValue {
        let roles = ["state", "memory", "schedules", "agents", "skills", "telegram", "configuration_portable"]
        let metadata: RPCValue = .object(["kind": .string("profile.restore.activate"), "profile_id": .string("source_scope"), "generation": .number(2), "root": .string("/private/tmp/restored-fixture"), "root_identity": identity,
            "marker_sha256": .string(marker), "restored_profile_id": .string(String(repeating: "e", count: 32)), "source_manifest_sha256": .string(String(repeating: "f", count: 64)), "resource_fingerprint": .string(String(repeating: "0", count: 64)),
            "resources": .array(roles.map { .object(["role": .string($0), "present": .bool(false), "sha256": .null, "byte_length": .number(0), "counts": .array([])]) }),
            "policy": .string("local_admission_only_no_automatic_effects"), "workspace_policy": .string("new_private_empty"), "credentials_included": .bool(false), "authenticity": .string("not_established")])
        return .object(["review_id": .string(reviewID), "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata))), "expires_in_seconds": .number(120), "metadata": metadata])
    }
    static func receipt() -> RPCValue {
        .object(["operation_id": .string(operationID), "purpose": .string("profile.restore.activate"), "status": .string("completed"), "effect_status": .string("local_admission_published"), "target_identity": identity, "marker_sha256": .string(marker), "admission_sha256": .string(admission), "error_code": .null, "cleanup_pending": .bool(false), "cleanup_error_code": .null, "services_started": .bool(false), "grants_restored": .bool(false)])
    }
    static func operation(_ changes: [String: RPCValue] = [:]) throws -> RPCValue {
        let review = try RestoreProfileReview(reviewValue())
        var fields: [String: RPCValue] = ["operation_id": .string(operationID), "kind": .string("profile.restore.activate"), "phase": .string("activated_not_opened"), "stage": .null, "result": .null, "error_code": .null, "receipt": receipt(), "receipt_durable": .bool(true), "worker_pending": .bool(false), "cancellation_requested": .bool(false), "scope": scope, "review_id": .string(review.id), "review_digest": .string(review.digest), "cleanup_owner_pending": .bool(false)]
        fields.merge(changes) { _, new in new }; return .object(fields)
    }
    static func target() throws -> ProfileOpenTarget { try ProfileOpenTarget(review: RestoreProfileReview(reviewValue()), operation: MaintenanceOperation(operation())) }
    static func localAdmission() -> RPCValue {
        .object(["root_identity": identity, "marker_sha256": .string(marker), "restored_profile_id": .string(String(repeating: "e", count: 32)), "source_manifest_sha256": .string(String(repeating: "f", count: 64)), "admission_sha256": .string(admission), "operation_id": .string(operationID), "initial_review_fingerprint": .string(String(repeating: "0", count: 64)), "state": .string("local_present_valid"), "authenticity": .string("not_established"), "prior_operation_durability": .string("not_established"), "grants_restored": .bool(false)])
    }
}

final class RestoredProfileTests: XCTestCase {
    private func options() throws -> LaunchOptions {
        try LaunchOptions.parse(["--python", "/usr/bin/python3", "--source", "/private/tmp/source", "--state", "/private/tmp/old", "--codex", "/private/tmp/codex", "--provider-state", "/private/tmp/old-account"])
    }
    func testConstrainedLaunchDropsPreviousProviderAndRequiresCompleteExactProof() throws {
        let target = try RestoreFixture.target(), options = try options().openingRestored(target)
        XCTAssertNil(options.providerState); XCTAssertEqual(options.state, target.path)
        XCTAssertEqual(options.expectedRestore?.identity, target.identity)
        XCTAssertEqual(options.expectedRestore?.operationID, target.operationID)
        XCTAssertFalse(options.serviceArguments.contains("/private/tmp/old-account"))
        let args = ["--python", options.python, "--source", options.source, "--state", options.state, "--codex", options.codex] + options.expectedRestore!.arguments
        XCTAssertEqual(try LaunchOptions.parse(args), options)
        XCTAssertThrowsError(try LaunchOptions.parse(Array(args.dropLast(2))))
        XCTAssertThrowsError(try LaunchOptions.parse(args + ["--provider-state", "/private/tmp/old-account"]))
        var wrong = args; wrong[wrong.firstIndex(of: "--expected-restore-inode")! + 1] = "1.0"
        XCTAssertThrowsError(try LaunchOptions.parse(wrong))
    }
    func testTargetRequiresExactDurableCompletedOperationAndNoCleanupOwner() throws {
        let review = try RestoreProfileReview(RestoreFixture.reviewValue())
        for change: [String: RPCValue] in [["receipt_durable": .bool(false)], ["cleanup_owner_pending": .bool(true)], ["review_id": .string(String(repeating: "9", count: 32))], ["scope": .object(["profile_id": .string("other"), "generation": .number(2)])]] {
            XCTAssertThrowsError(try ProfileOpenTarget(review: review, operation: MaintenanceOperation(RestoreFixture.operation(change))))
        }
        var receipt = RestoreFixture.receipt().object!
        receipt["target_identity"] = .object(["device": .string("1"), "inode": .string("999")])
        XCTAssertThrowsError(try ProfileOpenTarget(review: review, operation: MaintenanceOperation(RestoreFixture.operation(["receipt": .object(receipt)]))))
    }
    func testExpiredActivationReviewCannotBorrowALongerLivedApprovalTicket() throws {
        let now = Date(timeIntervalSince1970: 1_000)
        let payload = try RestoreFixture.reviewValue()
        let review = try RestoreProfileReview(payload, now: now)
        let operation = try MaintenanceOperation(RestoreFixture.operation([
            "phase": .string("awaiting_owner"), "receipt": .null, "receipt_durable": .bool(false)]))
        let arguments = try MaintenanceValidation.canonical(.object([
            "review_id": payload["review_id"], "review_digest": payload["review_digest"],
            "review": payload["metadata"]]))
        let binding: RPCValue = .object(["domain": .string("jarvis.host.approval/1"),
            "profile": payload["metadata"]["profile_id"], "generation": payload["metadata"]["generation"],
            "purpose": .string(operation.kind), "operation": .string(operation.id), "arguments_json": .string(arguments)])
        let approval = try MaintenanceApproval(.object([
            "approval_id": .string(String(repeating: "9", count: 32)), "scope": RestoreFixture.scope,
            "purpose": .string(operation.kind), "operation_id": .string(operation.id),
            "arguments_json": .string(arguments),
            "digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(binding))),
            "expires_at_ns": .string("4102444800000000000")]))
        XCTAssertTrue(review.matches(approval, operation: operation, now: now.addingTimeInterval(119)))
        XCTAssertFalse(review.matches(approval, operation: operation, now: now.addingTimeInterval(120)))
        XCTAssertFalse(review.matches(approval, operation: operation, now: now.addingTimeInterval(121)))
        XCTAssertGreaterThan(approval.expiresAt, review.expiresAt)
    }
    func testUnknownReceiptCanOnlyProduceNewExplicitInspectionTargetWithoutRewritingIt() throws {
        let review = try RestoreProfileReview(RestoreFixture.reviewValue())
        var receipt = RestoreFixture.receipt().object!
        receipt["status"] = .string("unknown"); receipt["effect_status"] = .string("publication_unknown")
        receipt["cleanup_pending"] = .bool(true) // Historical immutable evidence; current owner is already closed.
        let unknown = try MaintenanceOperation(RestoreFixture.operation(["phase": .string("publication_unknown"), "receipt": .object(receipt)]))
        XCTAssertThrowsError(try ProfileOpenTarget(review: review, operation: unknown))
        let inspected = try RestoreProfileInspection(.object(["profile_path": .string(review.path), "root_identity": RestoreFixture.identity, "state": .string("local_present_valid"), "admission": RestoreFixture.localAdmission(), "cleanup_owner_pending": .bool(false), "open_available": .bool(true)]))
        let target = try ProfileOpenTarget(inspection: inspected)
        XCTAssertTrue(target.followsUnconfirmedOperation)
        XCTAssertEqual(unknown.activationReceipt?.status, "unknown")
        XCTAssertEqual(unknown.activationReceipt?.cleanupPending, true)
        XCTAssertFalse(unknown.cleanupOwnerPending)
        XCTAssertEqual(target.operationID, RestoreFixture.operationID)
        let ownerStillLive = try RestoreProfileInspection(.object(["profile_path": .string(review.path), "root_identity": RestoreFixture.identity, "state": .string("local_present_valid"), "admission": RestoreFixture.localAdmission(), "cleanup_owner_pending": .bool(true), "open_available": .bool(false)]))
        XCTAssertThrowsError(try ProfileOpenTarget(inspection: ownerStillLive))
    }
    func testLocalMetadataReadCallsOnlyLocalRPCAndPreservesFreshAuthority() async throws {
        let local: RPCValue = .object(["profile_id": .string("new_fresh_scope"), "generation": .number(0), "root_identity": RestoreFixture.identity, "restore_admission": RestoreFixture.localAdmission(), "provider_choice": .object(["required": .bool(true), "selected": .bool(false), "query_status_allowed": .bool(false), "revision": .number(0)])])
        let client = RestoreProfileClient(request: { method, params in
            XCTAssertEqual(method, "service.local_status"); XCTAssertTrue(params.isEmpty)
            return local
        })
        let value = try await client.localStatus()
        XCTAssertFalse(value.providerQueryAllowed); XCTAssertFalse(value.providerSelected)
        XCTAssertTrue(value.matches(try RestoreFixture.target()))
        XCTAssertEqual(value.scope.profileID, "new_fresh_scope")
    }
    @MainActor
    func testIdenticalChatIDsNeverTransferDraftsToRestoredProfile() throws {
        let archive = ProfileDraftArchive(), scope = SessionScope(sessionID: "same_session", temporary: false)
        let temporary = SessionScope(sessionID: "same_temp", temporary: true)
        var old = SessionDrafts(); old.set("BORRADOR ORIGEN", for: scope); old.set("TEMPORAL", for: temporary)
        let origin = try MaintenanceFileIdentity(.object(["device": .string("1"), "inode": .string("111")]))
        let destination = try MaintenanceFileIdentity(RestoreFixture.identity)
        try archive.archive(old, origin: origin)
        var restored = SessionDrafts()
        XCTAssertEqual(restored.value(for: scope), ""); XCTAssertNil(archive.drafts(for: destination))
        restored.set("BORRADOR NUEVO", for: scope)
        XCTAssertEqual(archive.drafts(for: origin)?.value(for: scope), "BORRADOR ORIGEN")
        XCTAssertEqual(archive.drafts(for: origin)?.value(for: temporary), "")
        XCTAssertEqual(restored.value(for: scope), "BORRADOR NUEVO")
        try archive.archive(SessionDrafts(), origin: origin)
        try archive.archive(restored, origin: origin)
        XCTAssertEqual(archive.snapshots(for: origin).map { $0.value(for: scope) }, ["BORRADOR ORIGEN", "BORRADOR NUEVO"])
    }
    func testLateVoiceSegmentWithSameSessionIDIsRejectedByRuntimeIdentity() throws {
        let scope = SessionScope(sessionID: "same_session", temporary: false), old = UUID(), new = UUID()
        let transcript = LocalVoiceTranscript(contextID: LocalVoiceDraftBridge.contextID(scope, runtimeID: old), text: "TEXTO ANTIGUO", completion: .recognizedFinal)
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "NUEVO", eligible: true, runtimeID: new))
        XCTAssertNotNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "ORIGEN", eligible: true, runtimeID: old))
    }
    func testDeferredUIIntentCannotSelectAnotherActiveProfileRuntime() throws {
        let origin = ProfileRuntimeGuard(), destination = ProfileRuntimeGuard()
        let capturedIntent = origin.id
        XCTAssertNoThrow(try origin.check(expectedID: capturedIntent))
        XCTAssertThrowsError(try destination.check(expectedID: capturedIntent))
        origin.retire()
        XCTAssertThrowsError(try origin.check(expectedID: capturedIntent))
    }
    func testRetiredTransportCannotReconnectOrDispatchADeferredEffect() async throws {
        let runtime = ProfileRuntimeGuard()
        let transport = LocalRPCClient(stateDirectory: "/private/tmp/does-not-exist-" + UUID().uuidString, runtime: runtime)
        runtime.retire()
        do { _ = try await transport.call("provider.login"); XCTFail("A retired runtime dispatched") }
        catch { XCTAssertTrue(error is CancellationError) }
        await transport.close()
        do { _ = try await transport.call("maintenance.decide"); XCTFail("Closing must not revive a retired transport") }
        catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertFalse(runtime.isActive)
    }
    @MainActor
    func testHandoffStartsExactlyOneFreshTargetOnlyAfterExactGracefulOwner() async throws {
        let gate = ProfileHandoffGate(), owner = UUID(), origin = try MaintenanceFileIdentity(.object(["device": .string("1"), "inode": .string("111")]))
        var retired = false, starts = 0
        try await gate.open(target: RestoreFixture.target(), source: options(), origin: origin, predecessor: ServiceOwnerIdentity(ownerID: owner, processIdentifier: 42), retireAndStop: {
            retired = true
            return ServiceStopReport(ownerID: owner, processIdentifier: 42, exit: ServiceChildExit(status: 0, wasSignalled: false), killAccepted: false)
        }, start: { target in
            XCTAssertTrue(retired); XCTAssertNil(target.providerState); XCTAssertEqual(target.expectedRestore?.admissionHash, RestoreFixture.admission); starts += 1
        })
        XCTAssertEqual(starts, 1); XCTAssertFalse(gate.inProgress)
    }
    @MainActor
    func testWrongOwnerUnknownSignalAndAbnormalExitNeverStartSuccessor() async throws {
        let gate = ProfileHandoffGate(), owner = UUID(), origin = try MaintenanceFileIdentity(.object(["device": .string("1"), "inode": .string("111")]))
        let cases = [ServiceStopReport(ownerID: UUID(), processIdentifier: 42, exit: ServiceChildExit(status: 0, wasSignalled: false), killAccepted: false), ServiceStopReport(ownerID: owner, processIdentifier: 99, exit: ServiceChildExit(status: 0, wasSignalled: false), killAccepted: false), ServiceStopReport(ownerID: owner, processIdentifier: 42, exit: nil, killAccepted: true), ServiceStopReport(ownerID: owner, processIdentifier: 42, exit: ServiceChildExit(status: nil, wasSignalled: nil), killAccepted: false), ServiceStopReport(ownerID: owner, processIdentifier: 42, exit: ServiceChildExit(status: 9, wasSignalled: true), killAccepted: true), ServiceStopReport(ownerID: owner, processIdentifier: 42, exit: ServiceChildExit(status: 1, wasSignalled: false), killAccepted: false)]
        var starts = 0
        for report in cases {
            do { try await gate.open(target: RestoreFixture.target(), source: options(), origin: origin, predecessor: ServiceOwnerIdentity(ownerID: owner, processIdentifier: 42), retireAndStop: { report }, start: { _ in starts += 1 }); XCTFail("Unconfirmed predecessor permitted successor") }
            catch { XCTAssertEqual(error as? ServiceProcessFailure, .cleanupUnconfirmed) }
        }
        XCTAssertEqual(starts, 0)
    }
    @MainActor
    func testCancelledWaiterStillWaitsOwnedStopAndNeverStartsSuccessor() async throws {
        let gate = ProfileHandoffGate(), owner = UUID(), origin = try MaintenanceFileIdentity(.object(["device": .string("1"), "inode": .string("111")]))
        var continuation: CheckedContinuation<ServiceStopReport?, Never>?
        var starts = 0
        let waiter = Task { @MainActor in
            try await gate.open(target: RestoreFixture.target(), source: options(), origin: origin, predecessor: ServiceOwnerIdentity(ownerID: owner, processIdentifier: 42), retireAndStop: { await withCheckedContinuation { continuation = $0 } }, start: { _ in starts += 1 })
        }
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while continuation == nil {
            guard ContinuousClock.now < deadline else { waiter.cancel(); throw RPCFailure.unavailable }
            await Task.yield()
        }
        waiter.cancel()
        XCTAssertTrue(gate.inProgress)
        continuation?.resume(returning: ServiceStopReport(ownerID: owner, processIdentifier: 42, exit: ServiceChildExit(status: 0, wasSignalled: false), killAccepted: false))
        do { try await waiter.value; XCTFail("Cancelled waiter opened target") } catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertEqual(starts, 0); XCTAssertFalse(gate.inProgress)
    }
}
