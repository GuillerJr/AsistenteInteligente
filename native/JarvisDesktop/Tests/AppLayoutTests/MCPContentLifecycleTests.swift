import AppKit
import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

private enum MCPContentFixture {
    static let serverID = "calificacion"
    static let toolHash = String(repeating: "c", count: 64)
    static let appID = String(repeating: "a", count: 32)
    static let runID = String(repeating: "b", count: 32)
    static let scope = SessionScope(sessionID: String(repeating: "d", count: 32), temporary: true)
    static func contentHash(_ revision: Int) -> String { String(repeating: revision == 1 ? "e" : "f", count: 64) }
    static func server(_ connected: Bool) -> RPCValue {
        .object(["server_id": .string(serverID), "command": .array([]),
            "protocol_version": .string("2025-11-25"), "transport": .string("streamable_http"),
            "endpoint": .string("https://example.com/mcp"), "allow_loopback": .bool(false), "oauth": .null,
            "connected": .bool(connected), "configured": .bool(true), "tool_count": .number(connected ? 1 : 0),
            "catalog_hash": .string(toolHash), "error": connected ? .null : .string("mcp_disconnected")])
    }
    static func catalog(_ revision: Int) -> RPCValue {
        .object(["server_id": .string(serverID), "catalog_hash": .string(contentHash(revision)),
            "generation": .number(Double(revision)), "tools": .array([]), "resources": .array([]),
            "resource_templates": .array([]), "prompts": .array([])])
    }
}

private actor MCPReplyGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var entered = false
    private var released = false
    func hold() async {
        entered = true
        guard !released, !Task.isCancelled else { return }
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation = $0 }
        } onCancel: { Task { await self.release() } }
    }
    func waitUntilHeld() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while !entered {
            try Task.checkCancellation()
            guard ContinuousClock.now < deadline else { throw RPCFailure.unavailable }
            await Task.yield()
        }
    }
    func release() { released = true; continuation?.resume(); continuation = nil }
}

private actor MCPContentWire {
    private var connected = true
    private var closeConfirmed = true
    private var revision = 1
    private var oauthStatus = "authorization_pending"
    private var methods: [String] = []
    private var heldMethod: String?
    private var gate: MCPReplyGate?
    func setConnected(_ value: Bool) { connected = value }
    func setCloseConfirmed(_ value: Bool) { closeConfirmed = value }
    func changeContent() { revision = 2 }
    func setOAuthStatus(_ value: String) { oauthStatus = value }
    func holdNext(_ method: String, gate: MCPReplyGate) { heldMethod = method; self.gate = gate }
    func requests() -> [String] { methods }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        methods.append(method)
        let result: RPCValue
        switch method {
        case "mcp.list":
            result = .object(["servers": .array([MCPContentFixture.server(connected)]),
                "supported_protocols": .array([.string("2025-11-25")])])
        case "mcp.catalog": result = MCPContentFixture.catalog(revision)
        case "mcp.disconnect": connected = false; result = .object(["disconnected": .bool(true)])
        case "mcp.apps.close": result = .object(["closed": .bool(closeConfirmed)])
        case "mcp.oauth.finish": result = .object(["status": .string(oauthStatus)])
        case "mcp.content.result":
            result = .object(["settled": .bool(true), "provenance": .string("external_unverified"),
                "error": .null, "payload": .object(["text": .string("CONTENIDO PROPIO ANTERIOR")])])
        default: throw RPCFailure.invalidResponse
        }
        if heldMethod == method, let gate { heldMethod = nil; self.gate = nil; await gate.hold() }
        return result
    }
}

final class MCPContentLifecycleTests: XCTestCase {
    @MainActor private func fixture() async throws -> (MCPStore, MCPContentWire) {
        let store = MCPStore(), wire = MCPContentWire()
        store.configure(MCPManagementClient(request: { try await wire.request($0, $1) }), runtimeIsActive: { true })
        store.context = MCPContentFixture.scope
        try await store.refresh()
        let server = try XCTUnwrap(store.servers.first)
        await store.loadCatalog(server)
        return (store, wire)
    }
    @MainActor private func presentOwnRenderer(_ store: MCPStore) async throws -> MCPAppRenderer {
        _ = NSApplication.shared
        let html = "<html><body>APP PROPIA JARVIS</body></html>"
        let document = try MCPAppDocument(id: MCPContentFixture.appID, serverID: MCPContentFixture.serverID,
            resourceURI: "ui://jarvis/qualification", html: html, sha256: ManagementValidation.digest(html),
            policy: MCPAppContentPolicy(connectDomains: ["https://example.com"]))
        store.app = document; store.appSurfaceVisible = true
        store.appCatalogHash = MCPContentFixture.contentHash(1); store.appSource = store.servers.first
        let renderer = MCPAppRenderer(document: document,
            isCurrent: { [weak store] in store?.app?.id == document.id && store?.appCleanupUnconfirmed == false },
            request: { _ in throw MCPAppFailure.unsupported })
        store.appRenderer = renderer
        do { try await renderer.start() }
        catch { await store.closeApplication(); throw error }
        return renderer
    }
    @MainActor private func withHeldReply<Value: Sendable>(
        _ gate: MCPReplyGate, pending: Task<Value, Never>, change: @MainActor () async throws -> Void
    ) async throws -> Value {
        do { try await gate.waitUntilHeld(); try Task.checkCancellation(); try await change() }
        catch { pending.cancel(); await gate.release(); _ = await pending.value; throw error }
        await gate.release(); return await pending.value
    }

    @MainActor func testDisconnectClosesTheActualRendererAndCapabilityBeforeTransport() async throws {
        let (store, wire) = try await fixture()
        let server = try XCTUnwrap(store.servers.first)
        let renderer = try await presentOwnRenderer(store)
        XCTAssertNotNil(renderer.webView)
        await store.disconnect(server)
        XCTAssertNil(renderer.webView)
        XCTAssertNil(store.app); XCTAssertNil(store.appRenderer)
        XCTAssertTrue(store.catalogs.isEmpty); XCTAssertTrue(store.catalogSources.isEmpty)
        XCTAssertFalse(store.appCleanupUnconfirmed); XCTAssertFalse(store.servers[0].connected)
        XCTAssertNil(store.errorMessage)
        let calls = await wire.requests()
        XCTAssertEqual(Array(calls.suffix(3)), ["mcp.apps.close", "mcp.disconnect", "mcp.list"])
        await store.close()
    }

    @MainActor func testUnconfirmedAppCloseBlocksDisconnectUntilExplicitReconciliation() async throws {
        let (store, wire) = try await fixture()
        let server = try XCTUnwrap(store.servers.first)
        let renderer = try await presentOwnRenderer(store)
        await wire.setCloseConfirmed(false)
        await store.disconnect(server)
        XCTAssertNil(renderer.webView)
        XCTAssertEqual(store.app?.id, MCPContentFixture.appID)
        XCTAssertNil(store.appRenderer)
        XCTAssertTrue(store.appCleanupUnconfirmed); XCTAssertTrue(store.blocksContextChanges)
        var calls = await wire.requests()
        XCTAssertFalse(calls.contains("mcp.disconnect"))
        await wire.setCloseConfirmed(true)
        await store.disconnect(server)
        calls = await wire.requests()
        XCTAssertEqual(calls.filter { $0 == "mcp.apps.close" }.count, 2)
        XCTAssertEqual(calls.filter { $0 == "mcp.disconnect" }.count, 1)
        XCTAssertNil(store.app); XCTAssertFalse(store.appCleanupUnconfirmed)
        await store.close()
    }

    @MainActor func testRefreshRetiresAppWhenSourceDisconnectsExternally() async throws {
        let (store, wire) = try await fixture()
        let renderer = try await presentOwnRenderer(store)
        await wire.setConnected(false)
        try await store.refresh()
        XCTAssertNil(renderer.webView); XCTAssertNil(store.app); XCTAssertNil(store.appRenderer)
        XCTAssertTrue(store.catalogs.isEmpty); XCTAssertFalse(store.servers[0].connected)
        let calls = await wire.requests()
        XCTAssertEqual(Array(calls.suffix(2)), ["mcp.list", "mcp.apps.close"])
        XCTAssertFalse(calls.contains("mcp.disconnect"))
        await store.close()
    }

    @MainActor func testLateCatalogIsDiscardedAndFreshCompoundContentHashStillLoads() async throws {
        let (store, wire) = try await fixture()
        let server = try XCTUnwrap(store.servers.first), gate = MCPReplyGate()
        await wire.holdNext("mcp.catalog", gate: gate)
        let pending = Task { @MainActor in await store.loadCatalog(server) }
        _ = try await withHeldReply(gate, pending: pending) { await store.disconnect(server) }
        XCTAssertTrue(store.catalogs.isEmpty)
        await wire.setConnected(true); try await store.refresh()
        let reconnected = try XCTUnwrap(store.servers.first)
        await store.loadCatalog(reconnected)
        XCTAssertEqual(store.catalogs[server.id]?.hash, MCPContentFixture.contentHash(1))
        XCTAssertNotEqual(store.catalogs[server.id]?.hash, reconnected.catalogHash,
            "The content hash and registry tool hash are distinct bindings")
        XCTAssertTrue(store.contentSourceIsCurrent(server: server.id, catalog: MCPContentFixture.contentHash(1)))
        await store.close()
    }

    @MainActor func testChangedContentCatalogClosesAppEvenWhenToolCatalogDidNotChange() async throws {
        let (store, wire) = try await fixture()
        let renderer = try await presentOwnRenderer(store)
        let server = try XCTUnwrap(store.servers.first)
        await wire.changeContent()
        await store.loadCatalog(server)
        XCTAssertNil(renderer.webView); XCTAssertNil(store.app)
        XCTAssertEqual(store.servers.first?.catalogHash, MCPContentFixture.toolHash)
        XCTAssertEqual(store.catalogs[server.id]?.hash, MCPContentFixture.contentHash(2))
        let calls = await wire.requests()
        XCTAssertEqual(Array(calls.suffix(2)), ["mcp.catalog", "mcp.apps.close"])
        await store.close()
    }

    @MainActor func testPendingContentIdentityBlocksMutationWithoutDispatchOrReplay() async throws {
        let (store, wire) = try await fixture()
        let server = try XCTUnwrap(store.servers.first)
        store.pendingContentRunID = MCPContentFixture.runID
        store.pendingContentServer = server.id; store.pendingContentCatalog = MCPContentFixture.contentHash(1)
        XCTAssertTrue(store.blocksContextChanges, "A missing ACK still retains the submitted identity")
        let before = await wire.requests()
        await store.disconnect(server)
        let added = await store.add(server.configuration)
        await store.setContext(SessionScope(sessionID: String(repeating: "9", count: 32), temporary: true))
        let after = await wire.requests()
        XCTAssertEqual(before, after); XCTAssertFalse(added)
        XCTAssertEqual(store.pendingContentRunID, MCPContentFixture.runID)
        XCTAssertEqual(store.context, MCPContentFixture.scope)
        XCTAssertTrue(store.blocksContextChanges)
        await store.close()
    }

    @MainActor func testSettledOldContentIsNotPublishedAfterSourceRetires() async throws {
        let (store, wire) = try await fixture()
        store.pendingContentRunID = MCPContentFixture.runID
        store.pendingContentServer = MCPContentFixture.serverID
        store.pendingContentCatalog = MCPContentFixture.contentHash(1)
        let gate = MCPReplyGate()
        await wire.holdNext("mcp.content.result", gate: gate)
        store.resumeContentRead()
        let pending = try XCTUnwrap(store.contentTask)
        _ = try await withHeldReply(gate, pending: pending) {
            await wire.setConnected(false); try await store.refresh()
        }
        XCTAssertNil(store.contentPreview); XCTAssertNil(store.pendingContentRunID)
        let calls = await wire.requests()
        XCTAssertEqual(calls.filter { $0 == "mcp.content.result" }.count, 1)
        XCTAssertFalse(calls.contains("mcp.content.request"))
        await store.close()
    }

    @MainActor func testOAuthLinkExistsOnlyForTheSamePendingFlowAndIsClearedOnTerminalStatus() async throws {
        for status in ["authorization_pending", "connected", "failed"] {
            let (store, wire) = try await fixture()
            let server = try XCTUnwrap(store.servers.first)
            let url = try XCTUnwrap(URL(string: "http://127.0.0.1:31991/authorize?state=SYNTHETIC"))
            store.oauthAuthorizationURLs[server.id] = url
            store.oauthStates[server.id] = .object(["flow_id": .string(MCPContentFixture.runID),
                "status": .string("authorization_pending")])
            await wire.setOAuthStatus(status)
            await store.finishOAuth(server)
            XCTAssertEqual(store.oauthAuthorizationURLs[server.id], status == "authorization_pending" ? url : nil)
            let calls = await wire.requests()
            XCTAssertEqual(calls.filter { $0 == "mcp.oauth.finish" }.count, 1)
            XCTAssertFalse(calls.contains("mcp.oauth.begin"), "Reopening an existing URL must not start another grant")
            await store.close()
            XCTAssertTrue(store.oauthAuthorizationURLs.isEmpty)
        }
    }

    @MainActor func testServerMutationAndCloseRetirePendingAuthorizationLinks() async throws {
        let (store, _) = try await fixture()
        let server = try XCTUnwrap(store.servers.first)
        let url = try XCTUnwrap(URL(string: "http://127.0.0.1:31991/authorize?state=SYNTHETIC"))
        store.oauthAuthorizationURLs[server.id] = url
        await store.disconnect(server)
        XCTAssertTrue(store.oauthAuthorizationURLs.isEmpty)
        store.oauthAuthorizationURLs[server.id] = url
        await store.close()
        XCTAssertTrue(store.oauthAuthorizationURLs.isEmpty)
    }
}
