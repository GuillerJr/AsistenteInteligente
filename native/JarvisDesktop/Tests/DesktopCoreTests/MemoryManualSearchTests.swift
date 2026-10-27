import Foundation
import XCTest
@testable import DesktopCore

private enum MemoryManualSearchFixture {
    static let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
    static func hit(score: Double = 0.75) -> RPCValue {
        let text = "NOTA SINTÉTICA 🧪", hash = ManagementValidation.digest(text)
        let document = RPCValue.object(["document_id": .string("own-note"), "key": .string("own-key"),
            "title": .string("Nota propia de prueba"), "source_uri": .string("jarvis://synthetic"),
            "revision": .number(1), "text_sha256": .string(hash)])
        return .object(["document": document, "excerpt": .string(text), "score": .number(score),
            "citation": .object(["document_id": .string("own-note"), "revision": .number(1),
                "text_sha256": .string(hash), "source_uri": .string("jarvis://synthetic"),
                "start_byte": .number(0), "end_byte": .number(Double(text.utf8.count)), "excerpt_sha256": .string(hash)])])
    }
    static func response(mode: String, rows: [RPCValue] = []) -> RPCValue {
        var value: [String: RPCValue] = ["mode": .string(mode), "matches": .array(rows), "generation": .number(3),
            "next_offset": .number(Double(rows.count)), "has_more": .bool(false)]
        if ["semantic_local", "hybrid_local"].contains(mode) {
            value["index_fingerprint"] = .string(String(repeating: "c", count: 64))
        }
        return .object(value)
    }
}

final class MemoryManualSearchTests: XCTestCase {
    func testRequestKeepsExactUTF8AndChoosesModeWithoutEnablingAutomaticRecall() throws {
        let query = "¿Dónde está el marcador 🧪?"
        for mode in MemoryRecallMode.allCases {
            let request = try MemoryManualSearchRequest(scope: MemoryManualSearchFixture.scope, query: query, mode: mode)
            XCTAssertEqual(request.params["mode"], .string(mode.rawValue))
            XCTAssertEqual(request.params["query"], .string(query)); XCTAssertEqual(request.params["temporary"], .bool(true))
            XCTAssertEqual(Set(request.params.keys), ["session_id", "temporary", "query", "mode", "offset"])
        }
        XCTAssertThrowsError(try MemoryManualSearchRequest(scope: MemoryManualSearchFixture.scope,
            query: String(repeating: "🧪", count: 129), mode: .semanticLocal))
    }
    func testResponseRejectsFallbackNonfiniteScoreAndDuplicateCitation() throws {
        let valid = MemoryManualSearchFixture.response(mode: "semantic_local", rows: [MemoryManualSearchFixture.hit()])
        let parsed = try MemoryManualSearchResponse(valid, requested: .semanticLocal)
        XCTAssertEqual(parsed.matches.count, 1); XCTAssertEqual(parsed.mode, "semantic_local")
        for mode in ["fts5_lexical", "lexical_scan", "hybrid_local"] {
            XCTAssertThrowsError(try MemoryManualSearchResponse(MemoryManualSearchFixture.response(mode: mode), requested: .semanticLocal))
        }
        XCTAssertThrowsError(try MemoryManualSearchResponse(MemoryManualSearchFixture.response(mode: "semantic_local",
            rows: [MemoryManualSearchFixture.hit(score: .nan)]), requested: .semanticLocal))
        XCTAssertThrowsError(try MemoryManualSearchResponse(MemoryManualSearchFixture.response(mode: "semantic_local",
            rows: [MemoryManualSearchFixture.hit(), MemoryManualSearchFixture.hit()]), requested: .semanticLocal))
        var missing = valid.object!
        missing.removeValue(forKey: "index_fingerprint")
        XCTAssertThrowsError(try MemoryManualSearchResponse(.object(missing), requested: .semanticLocal))
        missing["index_fingerprint"] = .string("invalid")
        XCTAssertThrowsError(try MemoryManualSearchResponse(.object(missing), requested: .semanticLocal))
    }
    func testLexicalModeAndPaginationAreValidatedRatherThanAssumedComplete() throws {
        for mode in ["fts5_lexical", "lexical_scan"] {
            XCTAssertEqual(try MemoryManualSearchResponse(MemoryManualSearchFixture.response(mode: mode), requested: .lexical).mode, mode)
        }
        var row = MemoryManualSearchFixture.response(mode: "hybrid_local").object!
        row["has_more"] = .bool(true)
        XCTAssertThrowsError(try MemoryManualSearchResponse(.object(row), requested: .hybridLocal))
        row["has_more"] = .bool(false); row["next_offset"] = .number(1)
        XCTAssertThrowsError(try MemoryManualSearchResponse(.object(row), requested: .hybridLocal))
    }
    func testCapacityAndMissingModelMessagesNeverPromiseFallbackOrTruncation() {
        XCTAssertTrue(MemoryManualSearchPresentation.queryTooLarge.contains("no se recortó"))
        XCTAssertTrue(MemoryManualSearchPresentation.error(RPCFailure.remote(-1, "invalid_query")).contains("no se cambió de modo"))
        XCTAssertTrue(MemoryManualSearchPresentation.error(RPCFailure.remote(-1, "missing_model")).contains("no se usó otro proveedor"))
        XCTAssertTrue(MemoryManualSearchPresentation.error(RPCFailure.remote(-1, "memory_index_capacity")).contains("límite de capacidad"))
    }
}
