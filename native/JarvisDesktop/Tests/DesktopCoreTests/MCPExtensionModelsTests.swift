import CryptoKit
import Foundation
import XCTest
@testable import DesktopCore

final class MCPExtensionModelsTests: XCTestCase {
    private let hash = String(repeating: "a", count: 64)
    func testRemoteConfigurationPreservesTransportAndNeverUsesShellOrCredentialURL() throws {
        for transport in [MCPTransport.streamableHTTP, .sse] {
            let value = try MCPConfiguration(serverID: "fixture", endpoint: "https://example.com/mcp", transport: transport, protocolVersion: .modern)
            XCTAssertTrue(value.command.isEmpty)
            XCTAssertEqual(value.params["transport"], .string(transport.rawValue))
            XCTAssertEqual(try MCPConfiguration(spec: .object(value.params)), value)
        }
        for url in ["http://example.com/mcp", "https://user:secret@example.com/mcp", "https://example.com/mcp?token=synthetic", "https://example.com/#fragment", "file:///tmp/fixture"] {
            XCTAssertThrowsError(try MCPConfiguration(serverID: "fixture", endpoint: url, transport: .sse, protocolVersion: .legacy))
        }
        XCTAssertThrowsError(try MCPConfiguration(serverID: "fixture", endpoint: "http://127.0.0.1:31991/mcp", transport: .sse, protocolVersion: .legacy))
        XCTAssertNoThrow(try MCPConfiguration(serverID: "fixture", endpoint: "http://127.0.0.1:31991/mcp", transport: .sse, protocolVersion: .legacy, allowLoopback: true))
        XCTAssertThrowsError(try MCPConfiguration(serverID: "fixture", endpoint: "http://192.168.1.1/mcp", transport: .sse, protocolVersion: .legacy, allowLoopback: true))
    }
    func testAppsDefaultPolicyDeniesNetworkAndAuthorityExpansion() throws {
        let policy = try MCPAppContentPolicy()
        XCTAssertTrue(policy.contentSecurityPolicy.contains("connect-src 'none'"))
        XCTAssertTrue(policy.contentSecurityPolicy.contains("frame-src 'none'"))
        XCTAssertTrue(policy.contentSecurityPolicy.contains("worker-src 'none'"))
        XCTAssertTrue(policy.contentSecurityPolicy.contains("base-uri 'none'"))
        for origin in ["https://*.example.com", "http://example.com", "https://127.0.0.1", "https://localhost", "https://example.com/a", "https://example.com?x=y", "https://example.com:444", "https://example.com\r\nconnect-src *"] {
            XCTAssertThrowsError(try MCPAppContentPolicy(connectDomains: [origin]), origin)
        }
        let allowed = try MCPAppContentPolicy(connectDomains: ["https://example.com/"], resourceDomains: ["https://example.org"])
        XCTAssertEqual(allowed.connectDomains, ["https://example.com"])
        XCTAssertTrue(allowed.contentSecurityPolicy.contains("connect-src https://example.com;"))
        XCTAssertThrowsError(try MCPAppContentPolicy(connectDomains: ["https://example.com", "https://example.com/"]))
        XCTAssertThrowsError(try MCPAppContentPolicy(.object(["frameDomains": .array([.string("https://example.com")])])) )
    }
    func testAppDocumentRequiresFullDigestAndBoundedRAMSource() throws {
        let html = "<p>Documento propio de prueba</p>"
        let policy = try MCPAppContentPolicy()
        let document = try MCPAppDocument(id: String(repeating: "1", count: 32), serverID: "fixture", resourceURI: "ui://fixture/view", html: html,
            sha256: ManagementValidation.digest(html), policy: policy)
        XCTAssertEqual(document.html, html)
        XCTAssertThrowsError(try MCPAppDocument(id: document.id, serverID: "fixture", resourceURI: "ui://fixture/view", html: html, sha256: hash, policy: policy))
        let tooLarge = String(repeating: "x", count: 98305)
        XCTAssertThrowsError(try MCPAppDocument(id: document.id, serverID: "fixture", resourceURI: "ui://fixture/view", html: tooLarge,
            sha256: ManagementValidation.digest(tooLarge), policy: policy))
    }
    func testAppVisibilityAndMessagesRejectAmbiguousAuthority() throws {
        XCTAssertTrue(try MCPAppBridgeMessage.appVisible(nil))
        XCTAssertFalse(try MCPAppBridgeMessage.appVisible(.array([.string("model")])))
        XCTAssertTrue(try MCPAppBridgeMessage.appVisible(.array([.string("app")])))
        for value in [RPCValue.array([]), .array([.string("app"), .string("app")]), .array([.string("admin")]), .bool(true)] {
            XCTAssertThrowsError(try MCPAppBridgeMessage.appVisible(value))
        }
        let valid: RPCValue = .object(["jsonrpc": .string("2.0"), "id": .number(3), "method": .string("tools/call"), "params": .object([:])])
        XCTAssertEqual(try MCPAppBridgeMessage(valid).id, .number(3))
        for id in [RPCValue.bool(true), .null, .number(1.5), .string("")] {
            var object = valid.object!; object["id"] = id
            XCTAssertThrowsError(try MCPAppBridgeMessage(.object(object)))
        }
        var object = valid.object!; object["auth"] = .string("not-an-authority")
        XCTAssertThrowsError(try MCPAppBridgeMessage(.object(object)))
    }
    func testManagedResourceValidatesPathBytesAndFullHashWithoutOpeningFiles() throws {
        let data = Data("Recurso propio 🧪".utf8)
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let value: RPCValue = .object(["path": .string("docs/nota.txt"), "bytes": .number(Double(data.count)),
            "sha256": .string(digest), "content_base64": .string(data.base64EncodedString())])
        XCTAssertEqual(try ExtensionResource(value, expectedPath: "docs/nota.txt").data, data)
        XCTAssertThrowsError(try ExtensionResource(value, expectedPath: "other.txt"))
        var changed = value.object!; changed["sha256"] = .string(hash)
        XCTAssertThrowsError(try ExtensionResource(.object(changed), expectedPath: "docs/nota.txt"))
        for path in ["/owner/file", "../escape", "a/../b", "a//b", "a\\b", "", "./file"] {
            XCTAssertFalse(ExtensionFileDescriptor.validPath(path))
        }
    }
    func testSkillArgumentsUseDeclaredTypesWithoutBooleanOrIntegerCoercion() throws {
        func argument(_ type: String, required: Bool = true) throws -> SkillArgumentDescriptor {
            try SkillArgumentDescriptor(.object(["name": .string("valor"), "type": .string(type), "required": .bool(required), "description": .string("Dato sintético")]))
        }
        XCTAssertEqual(try argument("integer").value("42"), .number(42))
        XCTAssertThrowsError(try argument("integer").value("1.5"))
        XCTAssertThrowsError(try argument("integer").value("9007199254740992"))
        XCTAssertEqual(try argument("boolean").value("false"), .bool(false))
        XCTAssertThrowsError(try argument("boolean").value("1"))
        XCTAssertNil(try argument("string", required: false).value(""))
        XCTAssertEqual(try argument("string").value(""), .string(""))
    }
    func testContentResultNeverTreatsServerTextAsVerifiedHostInstructions() throws {
        let result = try MCPContentResult(.object(["settled": .bool(true), "payload": .object(["text": .string("dato externo")]), "provenance": .string("external_unverified")]))
        XCTAssertTrue(result.quotedDraft.contains("no instrucciones de Jarvis"))
        XCTAssertThrowsError(try MCPContentResult(.object(["settled": .bool(true), "payload": .null, "provenance": .string("trusted")])) )
    }
    func testRelayRequiresExplicitLoopbackMetadataAndNeverAcceptsRemoteEndpoint() throws {
        func snapshot(_ endpoint: String) -> RPCValue { .object(["running": .bool(true), "endpoint": .string(endpoint), "export_count": .number(1), "remote_clients": .number(0), "token_available": .bool(true)]) }
        XCTAssertNoThrow(try MCPRelaySnapshot(snapshot("http://127.0.0.1:31991/mcp")))
        var changedCatalog = snapshot("http://127.0.0.1:31991/mcp").object!
        changedCatalog["requires_review"] = .bool(true); changedCatalog["token_available"] = .bool(false)
        XCTAssertTrue(try MCPRelaySnapshot(.object(changedCatalog)).requiresReview)
        changedCatalog["token_available"] = .bool(true)
        XCTAssertThrowsError(try MCPRelaySnapshot(.object(changedCatalog)), "A changed catalog must never offer the old bearer")
        for endpoint in ["http://192.168.1.1:31991/mcp", "https://example.com/mcp", "http://127.0.0.1:31991/mcp?token=hidden"] {
            XCTAssertThrowsError(try MCPRelaySnapshot(snapshot(endpoint)))
        }
    }
    func testHookMetadataSeparatesExplicitMCPActionFromPromptContext() throws {
        let mcp: RPCValue = .object(["event": .string("before_turn"), "sha256": .string(hash),
            "mcp_server_id": .string("fixture"), "tool": .string("echo"), "arguments": .object(["text": .string("Dato propio")])])
        let hook = try PluginHookDescriptor(mcp)
        XCTAssertEqual(hook.serverID, "fixture"); XCTAssertNil(hook.promptID)
        let prompt = try PluginHookDescriptor(.object(["event": .string("enable"), "sha256": .string(hash), "prompt_id": .string("contexto")]))
        XCTAssertNil(prompt.serverID); XCTAssertEqual(prompt.promptID, "contexto")
        var invalid = mcp.object!; invalid["sha256"] = .string("not-a-bound-hook")
        XCTAssertThrowsError(try PluginHookDescriptor(.object(invalid)))
    }
    func testCleanupConfirmationNeverClaimsAMutatorWasReplayedOrPublished() throws {
        let value: RPCValue = .object(["receipt": .object(["operation": .string("extensions_cleanup"), "status": .string("closed"),
            "cleanup_confirmed": .bool(true), "replayed_mutation": .bool(false)]), "plugins_pending": .bool(false), "skills_pending": .bool(false)])
        XCTAssertTrue(try ExtensionCleanupResult(value).closed)
        var mismatch = value.object!; mismatch["plugins_pending"] = .bool(true)
        XCTAssertThrowsError(try ExtensionCleanupResult(.object(mismatch)))
        var receipt = value["receipt"].object!; receipt["replayed_mutation"] = .bool(true)
        mismatch = value.object!; mismatch["receipt"] = .object(receipt)
        XCTAssertThrowsError(try ExtensionCleanupResult(.object(mismatch)))
    }
    func testResourceTemplatesAreExplicitVariablesAndNeverImplicitHostFileAccess() throws {
        let value: RPCValue = .object(["uriTemplate": .string("fixture://notes/{name}{?format}"), "name": .string("Nota sintética"),
            "mimeType": .string("text/plain"), "variables": .array([.string("name"), .string("format")])])
        let template = try MCPResourceTemplateDescriptor(value)
        XCTAssertEqual(template.variables, ["name", "format"])
        var duplicate = value.object!; duplicate["variables"] = .array([.string("name"), .string("name")])
        XCTAssertThrowsError(try MCPResourceTemplateDescriptor(.object(duplicate)))
        duplicate = value.object!; duplicate["uriTemplate"] = .string("fixture://notes/\0")
        XCTAssertThrowsError(try MCPResourceTemplateDescriptor(.object(duplicate)))
    }
    func testOAuthMultipleIssuersRemainUnselectedUntilAnExactReadyResponse() throws {
        let catalog: RPCValue = .object(["state": .string("choose_issuer"), "resource": .string("https://resource.example/mcp"),
            "issuers": .array([.string("https://first.example/issuer"), .string("https://second.example/issuer")]), "scopes": .array([])])
        let pending = try MCPOAuthDiscovery(catalog)
        XCTAssertTrue(pending.requiresSelection); XCTAssertNil(pending.issuer)
        XCTAssertFalse(pending.validatesSelection("https://first.example/issuer"))
        let ready = try MCPOAuthDiscovery(.object(["state": .string("ready"), "resource": .string(pending.resource),
            "issuer": .string("https://second.example/issuer"), "scopes": .array([])]))
        XCTAssertTrue(ready.validatesSelection("https://second.example/issuer"))
        XCTAssertFalse(ready.validatesSelection("https://first.example/issuer"))
        var malformed = catalog.object!; malformed["issuer"] = .string("https://first.example/issuer")
        XCTAssertThrowsError(try MCPOAuthDiscovery(.object(malformed)))
        malformed = catalog.object!; malformed["issuers"] = .array([.string("https://first.example"), .string("https://first.example")])
        XCTAssertThrowsError(try MCPOAuthDiscovery(.object(malformed)))
        malformed = catalog.object!; malformed["access_token"] = .string("SYNTHETIC-NOT-A-CREDENTIAL")
        XCTAssertThrowsError(try MCPOAuthDiscovery(.object(malformed)))
    }
    func testAppMIMEIsAlwaysNativeEvenWhenItsURIIsNotTheUIScheme() throws {
        let app = try MCPResourceDescriptor(.object(["uri": .string("fixture://app/view"), "name": .string("App propia"),
            "mimeType": .string("text/html;profile=mcp-app")]))
        XCTAssertTrue(app.isApp, "App HTML must never enter the generic resource preview")
        let template = try MCPResourceTemplateDescriptor(.object(["uriTemplate": .string("fixture://app/{id}"), "name": .string("App parametrizada"),
            "mimeType": .string("text/html; profile=\"mcp-app\""), "variables": .array([.string("id")])]))
        XCTAssertTrue(template.isApp, "App templates must require native admission, not generic template reads")
        let ordinary = try MCPResourceDescriptor(.object(["uri": .string("fixture://notes/one"), "name": .string("Nota"), "mimeType": .string("text/plain")]))
        XCTAssertFalse(ordinary.isApp)
    }
}
