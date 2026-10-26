import Foundation
import XCTest
@testable import DesktopCore

private actor RecoveryStartupWire {
    var methods: [String] = []
    var validHealth: Bool
    init(validHealth: Bool = true) { self.validHealth = validHealth }
    func call(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        guard params.isEmpty else { throw RPCFailure.invalidResponse }
        methods.append(method)
        if method == "health" {
            return .object(["service": .string(validHealth ? "jarvis" : "other"),
                            "protocol": .string("jarvis-local-rpc"), "version": .number(1)])
        }
        guard method == "service.local_status" else { throw RPCFailure.invalidResponse }
        return .object(["profile_id": .string("synthetic_recovery"), "generation": .number(3),
                        "root_identity": .object(["device": .string("1"), "inode": .string("2")]),
                        "restore_admission": .null,
                        "provider_choice": .object(["required": .bool(false), "selected": .bool(false),
                                                    "query_status_allowed": .bool(true), "revision": .number(0)])])
    }
}

final class RecoveryBootstrapTests: XCTestCase {
    func testRecoveryStartupAndMonitorOnlyInspectTransportAndLocalIdentity() async throws {
        let wire = RecoveryStartupWire()
        for _ in 0..<2 {
            let status = try await RecoveryBootstrap.inspect { try await wire.call($0, $1) }
            XCTAssertEqual(status.scope.profileID, "synthetic_recovery")
        }
        let trace = await wire.methods
        XCTAssertEqual(trace, ["health", "service.local_status", "health", "service.local_status"])
        // In particular no sessions.list, schedules.list, service.status or SDK.
    }

    func testWrongServiceNeverAdvancesToProfileOrStoreReads() async throws {
        let wire = RecoveryStartupWire(validHealth: false)
        do {
            _ = try await RecoveryBootstrap.inspect { try await wire.call($0, $1) }
            XCTFail("No se debe abrir un perfil contra otro servicio")
        } catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
        let trace = await wire.methods
        XCTAssertEqual(trace, ["health"])
    }

    func testRecoveryModeIsExplicitAndNeverBecomesAPythonOrPermissionFlag() throws {
        let required = ["--python", "/usr/bin/python3", "--source", "/private/tmp/source",
                        "--state", "/private/tmp/profile", "--codex", "/usr/bin/false"]
        let normal = try LaunchOptions.parse(required)
        let recovery = try LaunchOptions.parse(required + ["--recovery-only"])
        XCTAssertFalse(normal.recoveryOnly)
        XCTAssertTrue(recovery.recoveryOnly)
        XCTAssertEqual(normal.serviceArguments, recovery.serviceArguments)
        XCTAssertThrowsError(try LaunchOptions.parse(required + ["--recovery-only", "--recovery-only"]))
        XCTAssertThrowsError(try LaunchOptions.parse(required + ["--recovery-only", "true"]))
    }
}
