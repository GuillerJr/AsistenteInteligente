import CryptoKit
import Foundation
import XCTest
@testable import DesktopCore

final class NativeAutomationHostTests: XCTestCase {
    private let id = String(repeating: "a", count: 32)
    private let nonce = String(repeating: "b", count: 32)
    private func work(payload: String? = nil, arguments: [String: RPCValue]? = nil,
                      command: String = "browser_open") throws -> RPCValue {
        let args = arguments ?? ["operation_id": .string(id), "url": .string("https://example.com/")]
        let bytes = try JSONEncoder().encode(RPCValue.object(["command": .string(command), "arguments": .object(args)]))
        let body = payload ?? String(decoding: bytes, as: UTF8.self)
        let hash = SHA256.hash(data: Data(body.utf8)).map { String(format: "%02x", $0) }.joined()
        return .object(["operation_id": .string(id), "nonce": .string(nonce), "command": .string(command),
            "arguments": .object(args), "payload_json": .string(body), "digest": .string(hash),
            "remaining_seconds": .number(23.75)])
    }

    func testWorkValidatesExactBodyHashAndBindsClaimTuple() throws {
        let args: [String: RPCValue] = ["operation_id": .string(id), "url": .string("https://example.com/"), "number": .number(1)]
        let body = "{\"arguments\":{\"number\":1.0,\"url\":\"https://example.com/\",\"operation_id\":\"\(id)\"},\"command\":\"browser_open\"}"
        let value = try work(payload: body, arguments: args)
        let parsed = try NativeAutomationWork(value)
        XCTAssertEqual(parsed.id, id); XCTAssertEqual(parsed.nonce, nonce)
        XCTAssertEqual(parsed.arguments, args)
        XCTAssertEqual(parsed.params(hostID: "host"), ["host_id": .string("host"), "operation_id": .string(id),
            "nonce": .string(nonce), "digest": value["digest"]])
        var tampered = try XCTUnwrap(value.object)
        tampered["payload_json"] = .string(body + " ")
        XCTAssertThrowsError(try NativeAutomationWork(.object(tampered)))
        tampered = try XCTUnwrap(value.object); tampered["arguments"] = .object(["operation_id": .string(id)])
        XCTAssertThrowsError(try NativeAutomationWork(.object(tampered)))
    }

    func testWorkRejectsExpiredOversizedUnknownAndForeignPayloads() throws {
        let original = try work()
        for (key, value) in [("remaining_seconds", RPCValue.number(0)), ("remaining_seconds", .number(24.01)),
            ("remaining_seconds", .number(.infinity)), ("nonce", .string("wrong")),
            ("operation_id", .string(String(repeating: "c", count: 32))), ("digest", .string(String(repeating: "0", count: 64)))] {
            var changed = try XCTUnwrap(original.object); changed[key] = value
            XCTAssertThrowsError(try NativeAutomationWork(.object(changed)), key)
        }
        var extra = try XCTUnwrap(original.object); extra["permission"] = .bool(true)
        XCTAssertThrowsError(try NativeAutomationWork(.object(extra)))
        XCTAssertThrowsError(try NativeAutomationWork(work(command: "Runtime.evaluate")))
        let foreignBody = "{\"command\":\"browser_close\",\"arguments\":{\"operation_id\":\"\(id)\"}}"
        XCTAssertThrowsError(try NativeAutomationWork(work(payload: foreignBody)))
        let extraBody = "{\"command\":\"browser_open\",\"arguments\":{\"operation_id\":\"\(id)\",\"url\":\"https://example.com/\"},\"extra\":true}"
        XCTAssertThrowsError(try NativeAutomationWork(work(payload: extraBody)))
        XCTAssertThrowsError(try NativeAutomationWork(work(payload: String(repeating: " ", count: 131073))))
    }

    func testOnlySignedMediaWorkCanUseThe180SecondDecoderBudget() throws {
        let payload = Data("{\\rtf1 fixture}".utf8)
        var args = NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "fixture.rtf")
        args["operation_id"] = .string(id)
        var media = try XCTUnwrap(work(arguments: args, command: "media.inspect").object)
        media["remaining_seconds"] = .number(180)
        XCTAssertEqual(try NativeAutomationWork(.object(media)).command, "media.inspect")
        for seconds in [0, 180.01, Double.infinity] {
            media["remaining_seconds"] = .number(seconds)
            XCTAssertThrowsError(try NativeAutomationWork(.object(media)))
        }
        for command in NativeAutomationWork.commands.subtracting(["media.inspect"]) {
            var ordinary = try XCTUnwrap(work(command: command).object)
            ordinary["remaining_seconds"] = .number(24.01)
            XCTAssertThrowsError(try NativeAutomationWork(.object(ordinary)), command)
        }
        XCTAssertEqual(NativeAutomationWork.maximumSeconds("desktop.observe"), 24)
    }

    @MainActor func testTypedMediaFailuresAreKnownOnlyAfterAdmissionAndKeepStablePublicCodes() throws {
        for error in [NativeMediaIssue.limit as Error, NativeMediaIssue.encryptedPDF,
                      LocalFileSpeechIssue.authorizationRequired, LocalFileSpeechIssue.localeUnavailable] {
            let value = try XCTUnwrap(NativeAutomationHost.settledMediaFailure(error, command: "media.inspect", claimed: true))
            XCTAssertEqual(value.result["succeeded"], .bool(false)); XCTAssertEqual(value.result["verified"], .bool(false))
            XCTAssertEqual(value.result["effect_status"], .string("known"))
            XCTAssertFalse(value.message.isEmpty)
            let expected = (error as? NativeMediaIssue)?.code ?? (error as! LocalFileSpeechIssue).code
            XCTAssertEqual(value.result["error"], .string(expected))
            XCTAssertNil(NativeAutomationHost.settledMediaFailure(error, command: "media.inspect", claimed: false))
            XCTAssertNil(NativeAutomationHost.settledMediaFailure(error, command: "desktop.action", claimed: true))
        }
        let transport = NSError(domain: "fixture", code: 1, userInfo: [NSLocalizedDescriptionKey: "PRIVATE DIAGNOSTIC"])
        XCTAssertNil(NativeAutomationHost.settledMediaFailure(transport, command: "media.inspect", claimed: true))
        XCTAssertNil(NativeAutomationHost.settledMediaFailure(RPCFailure.disconnected, command: "media.inspect", claimed: true))
        XCTAssertNil(NativeAutomationHost.settledMediaFailure(CancellationError(), command: "media.inspect", claimed: true))
    }
}
