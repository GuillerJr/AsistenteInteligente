import Foundation
import XCTest
@testable import DesktopCore

final class MCPModelsTests: XCTestCase {
    private func snapshot(_ changes: [String: RPCValue] = [:]) -> RPCValue {
        .object([
            "server_id": .string("fixture"), "protocol_version": .string("2026-07-28"),
            "command": .array([.string("/private/tmp/jarvis-fixture"), .string("--modo"), .string("local")]),
            "connected": .bool(false), "configured": .bool(true), "tool_count": .number(0),
            "catalog_hash": .null, "error": .null
        ].merging(changes) { _, replacement in replacement })
    }

    func testArgumentsRemainAnArrayWithNoShellSplittingAndReviewRoundTrips() throws {
        let config = try MCPConfiguration(serverID: "fixture_2", executable: "/private/tmp/Jarvis Fixture",
                                          argumentsJSON: """
                                          ["two words", "$(synthetic)", "quote\\\"", "line\\nnext", "español 🦉", "\\u202efake"]
                                          """, protocolVersion: .modern)
        XCTAssertEqual(config.command[1], "two words")
        XCTAssertEqual(config.command[2], "$(synthetic)")
        XCTAssertEqual(config.command.count, 7)
        XCTAssertEqual(try JSONDecoder().decode([String].self, from: Data(config.commandJSON.utf8)), config.command)
        XCTAssertTrue(config.commandJSON.contains("\\u202e"))
        XCTAssertTrue(config.commandJSON.contains("\\u000a"))
        XCTAssertEqual(config.params["command"]?.array?.count, 7)
        XCTAssertEqual(config.params["protocol_version"]?.string, "2026-07-28")
    }

    func testMalformedCommandsIdentifiersAndArgumentsAreRejectedBeforeIPC() throws {
        for id in ["", "UPPER", "a-b", "a\n", String(repeating: "a", count: 25)] {
            XCTAssertThrowsError(try MCPConfiguration(serverID: id, command: ["/fixture"], protocolVersion: .legacy))
        }
        for command in [[], ["relative"], ["/bin/zsh"], ["/fixture", ""], ["/fixture", "nul\0"],
                        ["/fixture", String(repeating: "x", count: 8193)],
                        ["/fixture"] + Array(repeating: "x", count: 64),
                        ["/fixture"] + Array(repeating: String(repeating: "x", count: 8192), count: 4)] {
            XCTAssertThrowsError(try MCPConfiguration(serverID: "fixture", command: command, protocolVersion: .legacy))
        }
        for arguments in ["{}", "[1]", "[true]", "[null]", "[\"x\",]", "\"x\""] {
            XCTAssertThrowsError(try MCPConfiguration(serverID: "fixture", executable: "/fixture",
                                                      argumentsJSON: arguments, protocolVersion: .modern), arguments)
        }
        XCTAssertNoThrow(try MCPConfiguration(serverID: "a", command: ["/fixture"] + Array(repeating: "x", count: 63),
                                               protocolVersion: .legacy))
    }

    func testServerSnapshotRequiresHonestConnectionStatusAndSanitizedErrors() throws {
        let disconnected = try MCPServerSnapshot(snapshot())
        XCTAssertFalse(disconnected.connected)
        XCTAssertNil(disconnected.catalogHash)
        XCTAssertThrowsError(try MCPServerSnapshot(snapshot(["connected": .bool(true)])))
        XCTAssertThrowsError(try MCPServerSnapshot(snapshot(["tool_count": .number(1)])))
        XCTAssertThrowsError(try MCPServerSnapshot(snapshot(["connected": .string("true")])))
        XCTAssertThrowsError(try MCPServerSnapshot(snapshot(["configured": .bool(false)])))
        XCTAssertThrowsError(try MCPServerSnapshot(snapshot(["error": .string("raw peer output /secret")])))
        XCTAssertThrowsError(try MCPServerSnapshot(snapshot(["error": .string("mcp_error\n")])))
        let connected = try MCPServerSnapshot(snapshot(["connected": .bool(true), "tool_count": .number(2),
                                                        "catalog_hash": .string(String(repeating: "a", count: 64))]))
        XCTAssertEqual(connected.toolCount, 2)
        XCTAssertTrue(connected.connected)
        XCTAssertEqual(try MCPServerSnapshot(snapshot(["error": .string("mcp_disconnected")])).errorCode, "mcp_disconnected")
        XCTAssertFalse(MCPPresentation.serverError("untrusted_peer").contains("untrusted_peer"))
    }

    func testCatalogRequiresExplicitSupportedProtocolsAndUniqueServers() throws {
        let valid: RPCValue = .object(["servers": .array([snapshot()]),
                                      "supported_protocols": .array([.string("2026-07-28"), .string("2025-11-25")])])
        let parsed = try MCPCatalog(valid)
        XCTAssertEqual(parsed.supportedProtocols, [.modern, .legacy])
        XCTAssertEqual(parsed.servers.count, 1)
        XCTAssertThrowsError(try MCPCatalog(.object(["servers": .array([snapshot(), snapshot()]),
                                                     "supported_protocols": .array([.string("2026-07-28")])])))
        let unsupported: [[RPCValue]] = [[], [.string("latest")], [.string("2025-11-25")],
                                          [.string("2026-07-28"), .string("2026-07-28")]]
        for versions in unsupported {
            XCTAssertThrowsError(try MCPCatalog(.object(["servers": .array([snapshot()]),
                                                         "supported_protocols": .array(versions)])))
        }
    }

    func testProviderStateIsOnlyAnExplicitOptionalLaunchFlag() throws {
        let base = ["--python", "/python", "--source", "/source", "--state", "/new-state", "--codex", "/codex"]
        XCTAssertNil(try LaunchOptions.parse(base).providerState)
        XCTAssertEqual(try LaunchOptions.parse(base + ["--provider-state", "/explicit-provider"]).providerState,
                       "/explicit-provider")
        XCTAssertThrowsError(try LaunchOptions.parse(base + ["--provider-state", "relative"]))
        XCTAssertThrowsError(try LaunchOptions.parse(base + ["--provider-state", "/a", "--provider-state", "/b"]))
    }
}
