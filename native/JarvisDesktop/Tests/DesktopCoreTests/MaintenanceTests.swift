import Foundation
import XCTest
@testable import DesktopCore

private actor MaintenanceRPCFixture {
    var calls: [(String, [String: RPCValue])] = []
    var replies: [RPCValue]
    var failure: RPCFailure?
    init(_ replies: [RPCValue] = [], failure: RPCFailure? = nil) { self.replies = replies; self.failure = failure }
    func call(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        calls.append((method, params))
        if let failure { throw failure }
        guard !replies.isEmpty else { throw RPCFailure.invalidResponse }
        return replies.removeFirst()
    }
    func recorded() -> [(String, [String: RPCValue])] { calls }
}

final class MaintenanceTests: XCTestCase {
    private func changed(_ value: RPCValue, _ updates: [String: RPCValue]) -> RPCValue {
        .object(value.object!.merging(updates) { _, new in new })
    }
    private func operation(phase: String = "awaiting_owner", pending: Bool = true, kind: String = "backup.create", result: RPCValue = .null) -> RPCValue {
        .object(["operation_id": .string(String(repeating: "b", count: 32)),
            "scope": .object(["profile_id": .string("synthetic_profile"), "generation": .number(7)]),
            "review_id": ["backup.create", "restore.stage"].contains(kind) ? MaintenanceFixtures.review["review_id"] : .null,
            "review_digest": ["backup.create", "restore.stage"].contains(kind) ? MaintenanceFixtures.review["review_digest"] : .null,
            "kind": .string(kind), "phase": .string(phase), "stage": .null, "result": result, "error_code": .null, "receipt": .null, "receipt_durable": .bool(false), "worker_pending": .bool(pending), "cancellation_requested": .bool(false)])
    }
    private var status: RPCValue { .object(["operations": .array([]), "previous_operations": .array([]), "profile_id": .string("synthetic_profile"), "generation": .number(7), "closing": .bool(false)]) }
    private var verification: RPCValue {
        .object(["verification_id": .string(String(repeating: "d", count: 32)), "verification": .object([
            "artifact_id": .string(String(repeating: "e", count: 32)), "profile_id": .string("synthetic_profile"), "generation": .number(7), "manifest_sha256": .string(String(repeating: "f", count: 64)), "root_identity": .object(["device": .string("1"), "inode": .string("18446744073709551615")]), "resource_hashes": .array([.array([.string("agents"), .string(String(repeating: "f", count: 64))])]), "excluded": .array(MaintenanceValidation.exclusions.sorted().map(RPCValue.string)), "authenticity": .string("not_established")])])
    }
    func testPythonCanonicalReviewAndHostTicketMatchExactMetadata() throws {
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        let approval = try MaintenanceApproval(MaintenanceFixtures.approval)
        let job = try MaintenanceOperation(operation())
        XCTAssertTrue(approval.matches(review, operation: job))
        XCTAssertEqual(review.destinationParent, "/private/tmp/prueba-Ω")
        XCTAssertEqual(review.destinationIdentity.inode, "18446744073709551615")
        XCTAssertEqual(approval.scope.generation, 7)
    }
    func testReviewRejectsChangedDestinationFlagsSourceOrDigest() {
        for metadata in [changed(MaintenanceFixtures.review["metadata"], ["destination_name": .string("other")]), changed(MaintenanceFixtures.review["metadata"], ["credentials_included": .bool(true)]), changed(MaintenanceFixtures.review["metadata"], ["source": .array([])]), changed(MaintenanceFixtures.review["metadata"], ["unexpected_secret": .string("PRIVATE")])] {
            XCTAssertThrowsError(try MaintenanceReview(changed(MaintenanceFixtures.review, ["metadata": metadata])))
        }
        XCTAssertThrowsError(try MaintenanceReview(changed(MaintenanceFixtures.review, ["expires_in_seconds": .number(121)])))
    }
    func testPendingRejectsTamperedArgumentsScopeDigestAndNumericExpiry() {
        for updates in [["digest": RPCValue.string(String(repeating: "d", count: 64))], ["scope": .object(["profile_id": .string("different"), "generation": .number(7)])], ["arguments_json": .string("{\"private\":\"body\"}")], ["expires_at_ns": .number(1_900_000_000_000_000_000)]] {
            XCTAssertThrowsError(try MaintenanceApproval(changed(MaintenanceFixtures.approval, updates)))
        }
    }
    func testApproveUsesCapturedTicketScopeDigestOnlyAndNeverCallsProvider() async throws {
        let fixture = MaintenanceRPCFixture([.object(["accepted": .bool(true)])])
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        let approval = try MaintenanceApproval(MaintenanceFixtures.approval)
        let job = try MaintenanceOperation(operation())
        let accepted = try await client.decide(approval, review: review, operation: job, allow: true)
        XCTAssertTrue(accepted)
        let calls = await fixture.recorded()
        XCTAssertEqual(calls.count, 1); XCTAssertEqual(calls[0].0, "maintenance.decide")
        XCTAssertEqual(calls[0].1, ["approval_id": .string(approval.id), "digest": .string(approval.digest), "allow": .bool(true), "profile_id": .string("synthetic_profile"), "generation": .number(7)])
    }
    func testChangedOperationOrExpiredApprovalIsRejectedBeforeWire() async throws {
        let fixture = MaintenanceRPCFixture()
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        let approval = try MaintenanceApproval(MaintenanceFixtures.approval)
        let wrong = try MaintenanceOperation(changed(operation(), ["operation_id": .string(String(repeating: "f", count: 32))]))
        do { _ = try await client.decide(approval, review: review, operation: wrong, allow: true); XCTFail("accepted other job") } catch { }
        let job = try MaintenanceOperation(operation())
        do { _ = try await client.decide(approval, review: review, operation: job, allow: true, now: Date(timeIntervalSince1970: 2_000_000_000)); XCTFail("accepted expired ticket") } catch { }
        let calls = await fixture.recorded(); XCTAssertTrue(calls.isEmpty)
    }
    func testLostSubmitAcknowledgementIsNotReplayed() async throws {
        let fixture = MaintenanceRPCFixture(failure: .disconnected)
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        do { _ = try await client.submit(review); XCTFail("succeeded") } catch { }
        let calls = await fixture.recorded(); XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0].0, "backup.create"); XCTAssertEqual(calls[0].1, review.params)
    }
    func testPureStatusAndPendingNeverSubmitConnectOrApprove() async throws {
        let pending: RPCValue = .object(["profile_id": .string("synthetic_profile"), "generation": .number(7), "pending": .array([])])
        let fixture = MaintenanceRPCFixture([status, pending])
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        _ = try await client.status(); _ = try await client.pending()
        let calls = await fixture.recorded()
        XCTAssertEqual(calls.map(\.0), ["maintenance.status", "maintenance.pending"])
        XCTAssertTrue(calls.allSatisfy { $0.1.isEmpty })
    }
    func testReviewAndVerificationHaveTypedPathAndJobBinding() async throws {
        let fixture = MaintenanceRPCFixture([MaintenanceFixtures.review, operation(kind: "backup.verify")])
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        _ = try await client.reviewBackup(parent: "/private/tmp/prueba-Ω", name: "jarvis-copia")
        _ = try await client.verify(path: "/private/tmp/jarvis-copia", expectedHash: String(repeating: "a", count: 64))
        do { _ = try await client.verify(path: "../auth.json", expectedHash: nil); XCTFail("relative accepted") } catch { }
        let calls = await fixture.recorded()
        XCTAssertEqual(calls.map(\.0), ["backup.review", "backup.verify"])
        XCTAssertEqual(calls[1].1["expected_manifest_sha256"], .string(String(repeating: "a", count: 64)))
    }
    func testOperationResponseCannotSwitchIdentityOrKind() async throws {
        let id = String(repeating: "f", count: 32)
        let fixture = MaintenanceRPCFixture([operation(), operation(kind: "doctor.report")])
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        do { _ = try await client.operation(id); XCTFail("switched id") } catch { }
        do { _ = try await client.submit(MaintenanceReview(MaintenanceFixtures.review)); XCTFail("switched kind") } catch { }
    }
    func testVerificationIDIsReadFromCompletedResultAfterWorkerSettlement() throws {
        let waiting = try MaintenanceOperation(operation(phase: "complete", pending: true, kind: "backup.verify", result: verification))
        XCTAssertFalse(waiting.settled); XCTAssertFalse(waiting.success)
        let settled = try MaintenanceOperation(operation(phase: "complete", pending: false, kind: "backup.verify", result: verification))
        XCTAssertTrue(settled.success)
        XCTAssertEqual(try MaintenanceVerification(settled.result).id, String(repeating: "d", count: 32))
        XCTAssertThrowsError(try MaintenanceOperation(operation(phase: "complete", pending: false, kind: "backup.verify")))
    }
    func testPublishedEffectsRequireDurableReceiptAndNoCleanupUncertainty() {
        XCTAssertThrowsError(try MaintenanceOperation(operation(phase: "complete", pending: false)))
        XCTAssertThrowsError(try MaintenanceOperation(operation(phase: "staged_disarmed", pending: false)))
        let unknown = try! MaintenanceOperation(operation(phase: "publication_unknown", pending: false))
        XCTAssertTrue(unknown.settled); XCTAssertFalse(unknown.success)
        XCTAssertEqual(MaintenancePresentation.phase(unknown.phase), "Resultado sin confirmar")
    }
    func testClosingViewCannotResumeMonitorWhileWorkerStillPending() throws {
        let pending = try MaintenanceOperation(operation(phase: "denied", pending: true))
        XCTAssertTrue(MaintenanceMonitorPolicy.pauses(visible: false, operation: pending, requestInFlight: false, unresolvedResponse: false))
        let settled = try MaintenanceOperation(operation(phase: "denied", pending: false))
        XCTAssertFalse(MaintenanceMonitorPolicy.pauses(visible: false, operation: settled, requestInFlight: false, unresolvedResponse: false))
        XCTAssertTrue(MaintenanceMonitorPolicy.pauses(visible: true, operation: settled, requestInFlight: false, unresolvedResponse: false))
        XCTAssertTrue(MaintenanceMonitorPolicy.pauses(visible: false, operation: settled, requestInFlight: false, unresolvedResponse: true))
    }
    func testOnlyExactGateBusyResponsePreservesMonitorConnection() {
        XCTAssertTrue(MaintenanceMonitorPolicy.isTemporaryBusy(RPCFailure.remote(-32037, "profile_maintenance_busy")))
        for error in [RPCFailure.remote(-32037, "profile_identity_changed"), .remote(-32037, "profile_lease_closed"), .remote(-32040, "profile_maintenance_busy"), .disconnected] {
            XCTAssertFalse(MaintenanceMonitorPolicy.isTemporaryBusy(error))
        }
    }
    func testUnknownRemoteDiagnosticsAreNeverShownInMaintenanceUI() throws {
        XCTAssertFalse(MaintenancePresentation.error(RPCFailure.remote(-32040, "PRIVATE TOKEN CANARY")).contains("PRIVATE"))
        XCTAssertFalse(MaintenancePresentation.error(NSError(domain: "PRIVATE HISTORY", code: 1)).contains("PRIVATE"))
        let message = "No se pudo validar el journal SQLite; el diagnóstico no acredita recuperación. Consulta el resultado, no se repetirá automáticamente."
        for code in ["profile_cold_journal_checksum_invalid", "profile_cold_journal_changed", "profile_cold_journal_unsupported"] {
            XCTAssertEqual(MaintenancePresentation.operationError(code), message)
            XCTAssertEqual(MaintenancePresentation.error(RPCFailure.remote(-32040, code)), message)
        }
        let diagnostic = "profile_cold_journal_checksum_invalid"
        let rejected = try MaintenanceOperation(changed(operation(phase: "failed", pending: false, kind: "doctor.report"), ["error_code": .string(diagnostic)]))
        XCTAssertEqual(rejected.errorCode, diagnostic, "The view may show only the bounded machine code already validated by the operation DTO")
        XCTAssertThrowsError(try MaintenanceOperation(changed(operation(phase: "failed", pending: false, kind: "doctor.report"), ["error_code": .string("PRIVATE TOKEN CANARY")])))
    }
    func testUnavailableSourceDoesNotRequestOwnerApprovalOrInitializeIt() async throws {
        var metadata = MaintenanceFixtures.review["metadata"].object!
        var rows = metadata["source"]!.array!
        rows[1] = changed(rows[1], ["disposition": .string("unavailable")])
        metadata["source"] = .array(rows)
        let raw: RPCValue = .object(metadata)
        let review = try MaintenanceReview(changed(MaintenanceFixtures.review, ["metadata": raw, "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(raw)))]))
        let fixture = MaintenanceRPCFixture()
        let client = MaintenanceClient(request: { try await fixture.call($0, $1) })
        do { _ = try await client.submit(review); XCTFail("requested approval with unavailable source") } catch { }
        let calls = await fixture.recorded(); XCTAssertTrue(calls.isEmpty)
        XCTAssertTrue(MaintenancePresentation.error(RPCFailure.remote(-32040, "profile_lazy_source_unavailable")).contains("no se modificó"))
    }
    func testValidDigestCannotAuthorizeCredentialInclusionOrActivation() throws {
        for flag in ["credentials_included", "workspace_files_included", "activation_included"] {
            let metadata = changed(MaintenanceFixtures.review["metadata"], [flag: .bool(true)])
            let review = changed(MaintenanceFixtures.review, ["metadata": metadata, "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(metadata)))])
            XCTAssertThrowsError(try MaintenanceReview(review))
        }
    }

    func testKnownRejectionDoesNotCreateFalseUnknownEffectLoop() {
        XCTAssertFalse(MaintenanceResponsePolicy.submissionMayBeUnconfirmed(RPCFailure.configuration))
        XCTAssertFalse(MaintenanceResponsePolicy.submissionMayBeUnconfirmed(RPCFailure.remote(-32040, "profile_lazy_source_unavailable")))
        XCTAssertTrue(MaintenanceResponsePolicy.submissionMayBeUnconfirmed(RPCFailure.disconnected))
        XCTAssertTrue(MaintenanceResponsePolicy.submissionMayBeUnconfirmed(RPCFailure.invalidResponse))
    }

    func testBuiltinAgentMetadataUsesSameExactWireShapeWithoutOpeningAStore() throws {
        var metadata = MaintenanceFixtures.review["metadata"].object!
        var rows = metadata["source"]!.array!
        rows[3] = changed(rows[3], ["disposition": .string("generated_builtin")])
        metadata["source"] = .array(rows)
        let value = RPCValue.object(metadata)
        let review = try MaintenanceReview(changed(MaintenanceFixtures.review, ["metadata": value, "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(value)))]))
        XCTAssertEqual(review.sources[3].disposition, "generated_builtin")
        XCTAssertEqual(review.sources.count, 6)
        let triple: RPCValue = .array([.string("agents"), .string("generated_builtin"), .number(2048)])
        rows[3] = triple; metadata["source"] = .array(rows)
        let invalid = RPCValue.object(metadata)
        XCTAssertThrowsError(try MaintenanceReview(changed(MaintenanceFixtures.review, ["metadata": invalid, "review_digest": .string(ManagementValidation.digest(try MaintenanceValidation.canonical(invalid)))])))
    }

    func testLostAckReconcilesDeniedCancelledFailedAndUnknownWithoutPendingTicket() throws {
        let review = try MaintenanceReview(MaintenanceFixtures.review, now: Date(timeIntervalSince1970: 1))
        XCTAssertLessThan(review.expiresAt, Date()) // Review itself already expired.
        for phase in ["denied", "cancelled", "failed", "publication_unknown", "cleanup_pending"] {
            let terminal = try MaintenanceOperation(operation(phase: phase, pending: false))
            let resolved = try MaintenanceSubmissionReconciliation.find(review, in: [terminal])
            XCTAssertEqual(resolved?.id, terminal.id)
            XCTAssertTrue(resolved?.settled == true)
            XCTAssertFalse(resolved?.success == true)
        }
    }
    func testLostAckReconcilesCompletedReceiptAfterLiveGenerationAdvanced() throws {
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        let receipt: RPCValue = .object([
            "operation_id": .string(String(repeating: "b", count: 32)), "purpose": .string("backup.create"),
            "status": .string("completed"), "effect_status": .string("published_verified_bytes"),
            "target_name": .string("jarvis-copia"), "artifact_id": .string(String(repeating: "d", count: 32)),
            "manifest_sha256": .string(String(repeating: "e", count: 64)),
            "target_identity": .object(["device": .string("1"), "inode": .string("2")]),
            "new_profile_id": .null, "error_code": .null, "cleanup_error_code": .null, "cleanup_pending": .bool(false)])
        let complete = changed(operation(phase: "complete", pending: false), ["receipt": receipt, "receipt_durable": .bool(true)])
        let live = try MaintenanceStatus(changed(status, ["generation": .number(9), "operations": .array([complete])]))
        XCTAssertNotEqual(live.scope.generation, review.scope.generation)
        let resolved = try MaintenanceSubmissionReconciliation.find(review, in: live.operations)
        XCTAssertTrue(resolved?.success == true)
        XCTAssertFalse(MaintenanceMonitorPolicy.pauses(visible: false, operation: resolved, requestInFlight: false, unresolvedResponse: false))
    }
    func testReconciliationRequiresExactProfileRevisionDigestAndKind() throws {
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        for change in [
            ["review_id": RPCValue.string(String(repeating: "f", count: 32))],
            ["review_digest": .string(String(repeating: "f", count: 64))],
            ["kind": .string("restore.stage")],
            ["scope": .object(["profile_id": .string("different_profile"), "generation": .number(7)])],
            ["scope": .object(["profile_id": .string("synthetic_profile"), "generation": .number(8)])]
        ] {
            let other = try MaintenanceOperation(changed(operation(), change))
            XCTAssertNil(try MaintenanceSubmissionReconciliation.find(review, in: [other]))
        }
        let own = try MaintenanceOperation(operation())
        XCTAssertThrowsError(try MaintenanceSubmissionReconciliation.find(review, in: [own, own]))
    }
    func testEvictedOrRestartedRequestHasNoEffectAbsenceInference() throws {
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        XCTAssertNil(try MaintenanceSubmissionReconciliation.find(review, in: []))
        let receiptWarning = MaintenanceSubmissionReconciliation.acknowledgement
        XCTAssertTrue(receiptWarning.contains("sigue sin confirmar"))
        // The resolver is local and consumes values, with no transport parameter.
        XCTAssertFalse(MaintenanceMonitorPolicy.pauses(visible: false, operation: nil, requestInFlight: false, unresolvedResponse: false))
    }
    func testSnapshotReadAndEffectReviewMetadataHaveDifferentStrictShapes() throws {
        XCTAssertThrowsError(try MaintenanceOperation(changed(operation(), ["review_id": .null])))
        XCTAssertThrowsError(try MaintenanceOperation(changed(operation(), ["review_digest": .null])))
        XCTAssertThrowsError(try MaintenanceOperation(changed(operation(kind: "doctor.report"), ["review_id": .string(String(repeating: "a", count: 32))])))
        XCTAssertThrowsError(try MaintenanceOperation(changed(operation(), ["scope": .null])))
        XCTAssertThrowsError(try MaintenanceStatus(changed(status, ["operations": .array([changed(operation(), ["scope": .object(["profile_id": .string("other"), "generation": .number(7)])])]) ])))
    }

    func testReadJobMayBindActualCutButEffectScopeCannotRetarget() throws {
        let before = try MaintenanceOperation(operation(phase: "quiescing", kind: "doctor.report"))
        let cut = try MaintenanceOperation(changed(operation(phase: "working", kind: "doctor.report"), ["scope": .object(["profile_id": .string("synthetic_profile"), "generation": .number(8)])]))
        XCTAssertTrue(cut.follows(before))
        XCTAssertFalse(before.follows(cut))
        let effectBefore = try MaintenanceOperation(operation())
        let effectAfter = try MaintenanceOperation(changed(operation(), ["scope": .object(["profile_id": .string("synthetic_profile"), "generation": .number(8)])]))
        XCTAssertFalse(effectAfter.follows(effectBefore))
        let approval = try MaintenanceApproval(MaintenanceFixtures.approval)
        let review = try MaintenanceReview(MaintenanceFixtures.review)
        XCTAssertFalse(approval.matches(review, operation: effectAfter))
    }

}
