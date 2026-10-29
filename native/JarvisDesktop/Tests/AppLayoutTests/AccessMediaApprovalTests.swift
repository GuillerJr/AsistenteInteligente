import Foundation
import XCTest
@testable import DesktopCore
@testable import JarvisApp

private enum MediaApprovalFixture {
    static let session = String(repeating: "a", count: 32)
    static let run = String(repeating: "b", count: 32)
    static let attachment = String(repeating: "c", count: 32)
    static let digest = String(repeating: "d", count: 64)
    static let scope = SessionScope(sessionID: session, temporary: true)
    static var arguments: [String: RPCValue] { ["attachment_id": .string(attachment), "sha256": .string(digest)] }
    static func operation(_ arguments: [String: RPCValue]) -> RPCValue {
        let json = ManagementValidation.json(.object(arguments))
        return .object(["approval_id": .string(String(repeating: "e", count: 32)),
            "session_id": .string(session), "temporary": .bool(true), "run_id": .string(run),
            "tool_name": .string("telegram_send_attachment"), "arguments_json": .string(json),
            "digest": .string(ManagementValidation.digest(json)),
            "expires_at": .number(Date().addingTimeInterval(120).timeIntervalSince1970)])
    }
    static func file(kind: String = "audio", mime: String = "audio/ogg", bytes: Int = 128) -> RPCValue {
        .object(["format": .string("jarvis-attachment/1"), "attachment_id": .string(attachment),
            "session_id": .string(session), "workspace_id": .string(String(repeating: "f", count: 64)),
            "temporary": .bool(true), "name": .string("prueba.ogg"), "kind": .string(kind), "mime": .string(mime),
            "byte_length": .number(Double(bytes)), "sha256": .string(digest), "source": .string("composer"), "revoked": .bool(false)])
    }
}

private actor AccessMediaApprovalWire {
    var operation: RPCValue
    var file: RPCValue
    var destinationRun = MediaApprovalFixture.run
    var pending = true
    var decisions: [[String: RPCValue]] = []
    init(arguments: [String: RPCValue], file: RPCValue = MediaApprovalFixture.file()) {
        operation = MediaApprovalFixture.operation(arguments); self.file = file
    }
    func changeArguments(_ arguments: [String: RPCValue]) { operation = MediaApprovalFixture.operation(arguments) }
    func changeDestinationRun() { destinationRun = String(repeating: "9", count: 32) }
    func decisionSnapshot() -> [[String: RPCValue]] { decisions }
    func request(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        switch method {
        case "approvals.list": return .object(["approvals": .array(pending ? [operation] : [])])
        case "media.list": return .object(["attachments": .array([file])])
        case "telegram.destination": return .object(["session_id": .string(MediaApprovalFixture.session),
            "run_id": .string(destinationRun), "chat_id": .number(-10073), "chat_type": .string("supergroup"),
            "message_id": .number(42), "message_thread_id": .number(8), "maximum_attachment_bytes": .number(5 * 1024 * 1024)])
        case "approvals.decide": decisions.append(params); pending = false; return .object(["accepted": .bool(true)])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class AccessMediaApprovalTests: XCTestCase {
    @MainActor private func makeStore(_ wire: AccessMediaApprovalWire) async throws -> AccessStore {
        let store = AccessStore(request: { try await wire.request($0, $1) })
        store.setContext(MediaApprovalFixture.scope)
        store.refreshCapabilities(.object(["tools": .bool(true)]))
        try await store.refreshApprovals(runID: MediaApprovalFixture.run)
        return store
    }

    @MainActor func testNativeReviewShowsExactModeLiteralCaptionAndPinnedDestination() async throws {
        for mode in [nil, "document", "voice"] as [String?] {
            var args = MediaApprovalFixture.arguments
            if let mode { args["delivery"] = .string(mode) }
            args["caption"] = .string("MARCADOR 6421\nTexto literal Ω")
            let wire = AccessMediaApprovalWire(arguments: args), store = try await makeStore(wire)
            let operation = try XCTUnwrap(store.approvals.first)
            var expected = "Archivo: prueba.ogg · 128 bytes\nSHA-256: \(MediaApprovalFixture.digest)"
            expected += "\nFormato de envío: \(mode == "voice" ? "nota de voz Ogg/Opus" : "archivo")."
            if mode == "voice" { expected += "\nAntes del envío se comprobará Opus y el límite de 120 segundos; si no cumple, no se enviará." }
            expected += "\nTexto adjunto literal: «MARCADOR 6421\nTexto literal Ω»"
            expected += "\nDestino fijado: Telegram supergroup, chat -10073, tema 8, respuesta al mensaje 42.\nSe enviarán los bytes de este archivo; una respuesta de Telegram no acredita su lectura por el destinatario."
            XCTAssertEqual(store.mediaApprovalDetails[operation.id], expected); XCTAssertTrue(store.mayApprove(operation))
            await store.decide(operation, allow: true)
            let decisions = await wire.decisionSnapshot()
            XCTAssertEqual(decisions, [operation.decision(allow: true)])
        }
    }

    @MainActor func testInvalidDeliveryCaptionOrVoiceDescriptorNeverBecomesApprovable() async throws {
        var cases: [([String: RPCValue], RPCValue)] = []
        for value in [RPCValue.null, .bool(true), .number(1), .string(""), .string("Voice"), .string("video")] {
            var args = MediaApprovalFixture.arguments; args["delivery"] = value
            cases.append((args, MediaApprovalFixture.file()))
        }
        for value in [RPCValue.null, .bool(true), .string("a\0b"), .string(String(repeating: "🟢", count: 513))] {
            var args = MediaApprovalFixture.arguments; args["caption"] = value
            cases.append((args, MediaApprovalFixture.file()))
        }
        var voice = MediaApprovalFixture.arguments; voice["delivery"] = .string("voice")
        cases += [(voice, MediaApprovalFixture.file(kind: "document")),
                  (voice, MediaApprovalFixture.file(mime: "audio/wav")),
                  (voice, MediaApprovalFixture.file(bytes: 5 * 1024 * 1024 + 1))]
        var unknown = MediaApprovalFixture.arguments; unknown["send_without_review"] = .bool(true)
        cases.append((unknown, MediaApprovalFixture.file()))
        for (args, file) in cases {
            let wire = AccessMediaApprovalWire(arguments: args, file: file), store = try await makeStore(wire)
            let operation = try XCTUnwrap(store.approvals.first)
            XCTAssertNil(store.mediaApprovalDetails[operation.id]); XCTAssertFalse(store.mayApprove(operation))
            await store.decide(operation, allow: true)
            let decisions = await wire.decisionSnapshot(); XCTAssertTrue(decisions.isEmpty)
        }
    }

    @MainActor func testDeliveryAndCaptionStayBoundToExactOperationAndCannotGrowRememberedAuthority() async throws {
        var args = MediaApprovalFixture.arguments; args["delivery"] = .string("document")
        args["caption"] = .string("TEXTO A")
        let wire = AccessMediaApprovalWire(arguments: args), store = try await makeStore(wire)
        let first = try XCTUnwrap(store.approvals.first)
        args["delivery"] = .string("voice"); args["caption"] = .string("TEXTO B")
        await wire.changeArguments(args); try await store.refreshApprovals(runID: MediaApprovalFixture.run)
        let replacement = try XCTUnwrap(store.approvals.first)
        XCTAssertNotEqual(first.digest, replacement.digest)
        await store.decide(first, allow: true)
        await store.decide(replacement, allow: true, remember: "session")
        await store.decide(replacement, allow: true, remember: "always")
        let decisions = await wire.decisionSnapshot(); XCTAssertTrue(decisions.isEmpty)
        XCTAssertTrue(store.mediaApprovalDetails[replacement.id]?.contains("«TEXTO B»") == true)
        XCTAssertFalse(store.mediaApprovalDetails[replacement.id]?.contains("TEXTO A") == true)
    }

    @MainActor func testChangedDestinationAtOwnerClickCannotApproveTheOldVoiceOperation() async throws {
        var args = MediaApprovalFixture.arguments; args["delivery"] = .string("voice")
        let wire = AccessMediaApprovalWire(arguments: args), store = try await makeStore(wire)
        let operation = try XCTUnwrap(store.approvals.first); XCTAssertTrue(store.mayApprove(operation))
        await wire.changeDestinationRun(); await store.decide(operation, allow: true)
        let decisions = await wire.decisionSnapshot(); XCTAssertTrue(decisions.isEmpty)
        XCTAssertNotNil(store.errorMessage)
    }
}
