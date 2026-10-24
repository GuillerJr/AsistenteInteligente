import Foundation
import XCTest
@testable import DesktopCore

final class CapabilityModelsTests: XCTestCase {
    private let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
    private let runID = String(repeating: "b", count: 32)

    private func approval(_ overrides: [String: RPCValue] = [:]) -> RPCValue {
        .object([
            "approval_id": .string(String(repeating: "c", count: 32)),
            "session_id": .string(scope.sessionID), "temporary": .bool(true),
            "run_id": .string(runID), "tool_name": .string("workspace_write"),
            "arguments_json": .string("{\"value\":9007199254740993}"),
            "digest": .string(String(repeating: "d", count: 64)),
            "expires_at": .number(2_000_000_000)
        ].merging(overrides) { _, updated in updated })
    }

    func testApprovalPreservesLargeIntegerTextAndBindsDecisionToScopeAndDigest() throws {
        let operation = try ApprovalOperation(approval(), expectedScope: scope, expectedRunID: runID)
        XCTAssertEqual(operation.argumentsJSON, "{\"value\":9007199254740993}")
        let decision = operation.decision(allow: true)
        XCTAssertEqual(decision["session_id"]?.string, scope.sessionID)
        XCTAssertEqual(decision["temporary"]?.bool, true)
        XCTAssertEqual(decision["digest"]?.string, String(repeating: "d", count: 64))
        XCTAssertEqual(decision["allow"]?.bool, true)
        XCTAssertNil(decision["arguments"])
    }

    func testApprovalRejectsAnotherScopeRunOrMalformedIdentity() {
        for override in [
            ["session_id": RPCValue.string(String(repeating: "f", count: 32))],
            ["temporary": RPCValue.bool(false)],
            ["run_id": RPCValue.string(String(repeating: "f", count: 32))],
            ["digest": RPCValue.string("not-a-digest")],
            ["arguments_json": RPCValue.string("[1,2,3]")],
            ["expires_at": RPCValue.number(-1)]
        ] {
            XCTAssertThrowsError(try ApprovalOperation(approval(override), expectedScope: scope, expectedRunID: runID))
        }
    }

    func testChangedDigestIsADifferentSnapshotAndExpirationIsExplicit() throws {
        let first = try ApprovalOperation(approval(), expectedScope: scope, expectedRunID: runID)
        let changed = try ApprovalOperation(approval(["digest": .string(String(repeating: "e", count: 64))]),
                                            expectedScope: scope, expectedRunID: runID)
        XCTAssertNotEqual(first, changed)
        XCTAssertNotEqual(first.decision(allow: true)["digest"], changed.decision(allow: true)["digest"])
        XCTAssertFalse(first.isExpired(at: Date(timeIntervalSince1970: 1_999_999_999)))
        XCTAssertTrue(first.isExpired(at: Date(timeIntervalSince1970: 2_000_000_000)))
    }

    func testDraftsAreIsolatedBySessionAndTemporaryMode() {
        var drafts = SessionDrafts()
        let persistent = SessionScope(sessionID: scope.sessionID, temporary: false)
        drafts.set("texto temporal", for: scope)
        drafts.set("texto durable", for: persistent)
        XCTAssertEqual(drafts.value(for: scope), "texto temporal")
        XCTAssertEqual(drafts.value(for: persistent), "texto durable")
        drafts.clearTemporary()
        XCTAssertEqual(drafts.value(for: scope), "")
        XCTAssertEqual(drafts.value(for: persistent), "texto durable")
        drafts.remove(persistent)
        XCTAssertEqual(drafts.value(for: persistent), "")
    }

    func testMemoryMatchCannotClaimCitationForDifferentDocumentRevisionOrBytes() throws {
        let hash = String(repeating: "a", count: 64)
        let document: RPCValue = .object([
            "document_id": .string("document"), "key": .string("notes"), "title": .string("Notas"),
            "source_uri": .string("workspace:notes"), "revision": .number(2), "text_sha256": .string(hash)
        ])
        let citation: [String: RPCValue] = [
            "document_id": .string("document"), "revision": .number(2), "text_sha256": .string(hash),
            "start_byte": .number(4), "end_byte": .number(8),
            "excerpt_sha256": .string("b221d9dbb083a7f33428d7c2a3c3198ae925614d70210e28716ccaa7cd4ddb79"),
            "source_uri": .string("workspace:notes")
        ]
        let match = try MemoryMatch(.object(["document": document, "excerpt": .string("hola"), "citation": .object(citation)]))
        XCTAssertEqual(match.id, "document:2:4:8")
        for override in [["revision": RPCValue.number(1)], ["end_byte": RPCValue.number(3)],
                         ["source_uri": RPCValue.string("another:source")],
                         ["excerpt_sha256": RPCValue.string(hash)]] {
            XCTAssertThrowsError(try MemoryMatch(.object([
                "document": document, "excerpt": .string("hola"),
                "citation": .object(citation.merging(override) { _, updated in updated })
            ])))
        }
    }

    func testReceiptRejectsSuccessForAnUnknownEffectAndSeparatesReportedFromVerified() throws {
        let base: [String: RPCValue] = [
            "operation_id": .string(String(repeating: "a", count: 32)),
            "tool_name": .string("workspace_read"), "succeeded": .bool(true), "verified": .bool(false),
            "effect_status": .string("known"), "output": .string("resultado"), "output_truncated": .bool(true)
        ]
        let reported = try ActionReceipt(.object(base))
        XCTAssertEqual(reported.label, "Resultado reportado, sin comprobación")
        XCTAssertTrue(reported.outputTruncated)
        XCTAssertThrowsError(try ActionReceipt(.object(base.merging(["effect_status": .string("unknown")]) { _, updated in updated })))
        let verified = try ActionReceipt(.object(base.merging(["verified": .bool(true)]) { _, updated in updated }))
        XCTAssertEqual(verified.label, "Resultado comprobado")
    }

    func testCapabilityErrorsStayLocalizedWithoutExposingUnknownMachineCodes() {
        XCTAssertTrue(CapabilityPresentation.errorMessage(RPCFailure.remote(-32010, "stale_citation")).contains("cambió"))
        XCTAssertTrue(CapabilityPresentation.errorMessage(RPCFailure.remote(-32010, "approval_digest_mismatch")).contains("No se autorizó"))
        XCTAssertFalse(CapabilityPresentation.errorMessage(RPCFailure.remote(-32010, "unknown_private_machine_code")).contains("unknown_private_machine_code"))
        XCTAssertEqual(CapabilityPresentation.errorMessage(RPCFailure.disconnected), RPCFailure.disconnected.localizedDescription)
    }

    func testInternalStartFailureAndLostAcknowledgementRemainUncertain() {
        XCTAssertFalse(RunPresentation.startWasRejected(RPCFailure.disconnected))
        XCTAssertFalse(RunPresentation.startWasRejected(RPCFailure.remote(-32603, "internal_error")))
        XCTAssertFalse(RunPresentation.startWasRejected(RPCFailure.remote(-32010, "unknown_write_failure")))
        XCTAssertTrue(RunPresentation.startWasRejected(RPCFailure.remote(-32602, "invalid_params")))
        XCTAssertTrue(RunPresentation.startWasRejected(RPCFailure.remote(-32011, "busy")))
    }
}
