import Foundation
import XCTest
@testable import AegisMenuBar

final class HealthReportTests: XCTestCase {
    private func report(state: String? = nil, fallback: Bool = false) throws -> JarvisHealthReport {
        var nvidia: [String: Any] = [
            "credential": "configured", "calls": 1, "answered": 1,
            "rate_limited": 0, "failed": 0, "cooldown_seconds": 0,
        ]
        if fallback {
            nvidia["last_fallback"] = [
                "role": "router", "requested_model_id": "test/selected",
                "actual_model_id": "test/backup", "reason": "timeout",
            ]
        }
        var payload: [String: Any] = [
            "window_hours": 24, "nvidia": nvidia, "mcp": [], "failures": [],
        ]
        if let state { payload["memory"] = ["state": state] }
        return try JSONDecoder().decode(
            JarvisHealthReport.self, from: JSONSerialization.data(withJSONObject: payload)
        )
    }

    func testOldHealthPayloadRemainsDecodable() throws {
        let parsed = try report()
        XCTAssertNil(parsed.memory)
        XCTAssertNil(parsed.nvidia?.last_fallback)
        XCTAssertFalse(parsed.lines.contains { $0.text == "Se usó un modelo de respaldo" })
    }

    func testFallbackShowsActualModelAndWarning() throws {
        let parsed = try report(fallback: true)
        XCTAssertTrue(parsed.lines.contains {
            $0.text == "Se usó un modelo de respaldo" && $0.tone == .warning
        })
        XCTAssertTrue(parsed.lines.contains {
            $0.text == "test/selected → test/backup" && $0.tone == .detail
        })
    }

    func testReadinessStatesNeverClaimHybridWhileIndexingOrDegraded() throws {
        for state in ["indexing", "degraded", "lexical_only", "future_state"] {
            let parsed = try report(state: state)
            XCTAssertFalse(parsed.lines.contains { $0.text == "Búsqueda híbrida preparada" })
        }
        XCTAssertTrue(try report(state: "hybrid_ready").lines.contains {
            $0.text == "Búsqueda híbrida preparada" && $0.tone == .good
        })
    }

    func testCodexHealthDoesNotRequireOrClaimNvidiaCredentials() throws {
        for state in ["ready", "signed_out", "unavailable", "future_state"] {
            let data = try JSONSerialization.data(withJSONObject: [
                "window_hours": 24, "inference": ["provider": "codex", "state": state],
                "mcp": [], "failures": [],
            ] as [String: Any])
            let parsed = try JSONDecoder().decode(JarvisHealthReport.self, from: data)
            XCTAssertNil(parsed.nvidia)
            XCTAssertFalse(parsed.lines.contains { $0.text == "Clave" || $0.text == "NVIDIA" })
            XCTAssertEqual(parsed.lines.contains { $0.text == "Chat" && $0.value == "Listo" }, state == "ready")
        }
    }
}
