import Foundation
import XCTest
@testable import DesktopCore

final class RPCFrameTests: XCTestCase {
    func testUnconfirmedOfficialClientCleanupBlocksNewMessagesAndKeepsTheRequestIdentity() {
        let error = RPCFailure.remote(-32020, "codex_cleanup_incomplete")
        let message = "El cliente oficial no confirmó su cierre. No se enviarán mensajes nuevos hasta que termine la limpieza."
        XCTAssertEqual(RunPresentation.errorMessage("codex_cleanup_incomplete"), message)
        XCTAssertEqual(CapabilityPresentation.errorMessage(error), message)
        XCTAssertFalse(RunPresentation.startWasRejected(error))
    }

    func testStreamingPreservesDeltasAndEnforcesUTF8Budget() throws {
        XCTAssertEqual(try RunPresentation.appendDelta(" mundo", to: "Hola"), "Hola mundo")
        XCTAssertThrowsError(try RunPresentation.appendDelta("á", to: String(repeating: "a", count: 31_999)))
        XCTAssertEqual(try RunPresentation.appendDelta("a", to: String(repeating: "a", count: 31_999)).utf8.count, 32_000)
    }

    func testKnownAndUnknownRunErrorsAreLocalizedWithoutExposingMachineData() {
        XCTAssertTrue(RunPresentation.errorMessage("codex_login_required").contains("Inicia sesión"))
        XCTAssertTrue(RunPresentation.errorMessage("codex_thread_recovery_required").contains("no se repetirá"))
        XCTAssertFalse(RunPresentation.errorMessage("PRIVATE-TEST-SENTINEL").contains("PRIVATE-TEST-SENTINEL"))
    }

    func testRequestContainsTypedParamsAuthenticationAndOneFrame() throws {
        let data = try RPCFrame.request(id: 7, method: "runs.start", params: [
            "message": .string("Primera línea\nSegunda línea"), "temporary": .bool(true)
        ], token: String(repeating: "a", count: 64))
        XCTAssertEqual(data.last, 10)
        XCTAssertFalse(data.dropLast().contains(10))
        let value = try JSONDecoder().decode(RPCValue.self, from: data)
        XCTAssertEqual(value["id"].integer, 7)
        XCTAssertEqual(value["method"].string, "runs.start")
        XCTAssertEqual(value["params"]["temporary"].bool, true)
        XCTAssertEqual(value["auth"].string?.count, 64)
    }

    func testResponseDecodesResultAndPreservesUnicode() throws {
        let data = Data("{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"text\":\"Sí, funciona\"}}\n".utf8)
        XCTAssertEqual(try RPCFrame.response(data, expectedID: 1)["text"].string, "Sí, funciona")
    }

    func testMalformedOrMismatchedEnvelopesRejected() {
        for raw in [
            "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":null}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":true,\"result\":null}\n",
            "{\"jsonrpc\":\"1.0\",\"id\":1,\"result\":null}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":null,\"error\":{}}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":null}",
            "[]\n", "{}\n{}\n", "invalid\n"
        ] { XCTAssertThrowsError(try RPCFrame.response(Data(raw.utf8), expectedID: 1)) }
    }

    func testRemoteErrorIsDistinctFromTransportFailure() {
        let data = Data("{\"jsonrpc\":\"2.0\",\"id\":1,\"error\":{\"code\":-32001,\"message\":\"No autorizado\"}}\n".utf8)
        XCTAssertThrowsError(try RPCFrame.response(data, expectedID: 1)) { error in
            XCTAssertEqual(error as? RPCFailure, .remote(-32001, "No autorizado"))
        }
    }

    func testRequestLimitsAndInvalidMethods() {
        let token = String(repeating: "a", count: 64)
        XCTAssertThrowsError(try RPCFrame.request(id: 1, method: "eval(code)", params: [:], token: token))
        XCTAssertThrowsError(try RPCFrame.request(id: 0, method: "health", params: [:], token: token))
        XCTAssertThrowsError(try RPCFrame.request(id: 1, method: "health", params: [:], token: "short"))
        XCTAssertThrowsError(try RPCFrame.request(id: 1, method: "echo", params: [
            "text": .string(String(repeating: "a", count: RPCFrame.limit))
        ], token: token))
    }

    func testLaunchRequiresFourExplicitAbsolutePaths() throws {
        let options = try LaunchOptions.parse(["--state", "/private/tmp/test", "--source", "/source",
                                               "--python", "/python", "--codex", "/codex"])
        XCTAssertEqual(options.state, "/private/tmp/test")
        XCTAssertThrowsError(try LaunchOptions.parse([]))
        XCTAssertThrowsError(try LaunchOptions.parse(["--state", "relative"]))
        XCTAssertThrowsError(try LaunchOptions.parse(["--state", "/tmp/a", "--state", "/tmp/b"]))
        XCTAssertThrowsError(try LaunchOptions.parse(["--unknown", "/value"]))
    }
}
