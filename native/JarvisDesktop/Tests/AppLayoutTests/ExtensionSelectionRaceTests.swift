import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

private enum ExtensionReadFixture {
    static let pluginID = "plugin_" + String(repeating: "a", count: 32)
    static let skillID = "skill_" + String(repeating: "b", count: 32)
    static func skillText(_ revision: Int) -> String { "SKILL PROPIA JARVIS V\(revision)" }
    static func plugin(_ revision: Int) -> RPCValue {
        .object(["plugin_id": .string(pluginID), "agent_id": .string("jarvis"),
            "package_id": .string("jarvis.qualification"), "name": .string("Plugin propio"),
            "description": .string("Datos de prueba"), "version": .string("\(revision).0.0"),
            "revision": .number(Double(revision)), "sha256": .string(ManagementValidation.digest("PACKAGE \(revision)")),
            "enabled": .bool(true), "skills_count": .number(1), "resources_count": .number(1),
            "hooks_count": .number(0), "mcp_count": .number(0),
            "source_uri": .string("https://example.com/jarvis"), "publisher": .string("Pruebas Jarvis"),
            "source_verified": .bool(false)])
    }
    static func skill(_ revision: Int) -> RPCValue {
        .object(["skill_id": .string(skillID), "agent_id": .string("jarvis"),
            "name": .string("Skill propia"), "description": .string("Datos de prueba"),
            "revision": .number(Double(revision)), "sha256": .string(ManagementValidation.digest(skillText(revision))),
            "enabled": .bool(true), "source_path": .string("/private/tmp/jarvis-synthetic-skill"),
            "source_verified": .bool(false), "resources": .array([.string("data.txt")])])
    }
    static func resource(_ revision: Int) -> RPCValue {
        let text = "RECURSO PROPIO JARVIS V\(revision)"
        return .object(["path": .string("data.txt"), "bytes": .number(Double(text.utf8.count)),
            "sha256": .string(ManagementValidation.digest(text)),
            "content_base64": .string(Data(text.utf8).base64EncodedString())])
    }
    static func response(_ method: String, revision: Int, params: [String: RPCValue]) throws -> RPCValue {
        switch method {
        case "plugins.resource", "skills.resource": return .object(["resource": resource(revision)])
        case "plugins.catalog":
            return .object(["catalog": .object(["files": .array([resource(revision)]),
                "skills": .array([]), "prompts": .array([]), "hooks": .array([]), "mcp": .array([])])])
        case "plugins.versions", "skills.versions":
            let row = method.hasPrefix("plugins") ? plugin(revision) : skill(revision)
            return .object(["versions": .array([.object(["version": .string("\(revision).0.0"),
                "sha256": row["sha256"], "current": .bool(true),
                "revision": method.hasPrefix("plugins") ? .null : .number(Double(revision))])])])
        case "skills.metadata":
            return .object(["metadata": .object(["version": .string("\(revision).0.0"),
                "argument_hint": .string(""), "user_invocable": .bool(true),
                "arguments": .array([]), "metadata": .object([:])])])
        case "skills.invoke":
            return .object(["invocation": .object(["skill_id": .string(skillID),
                "revision": .number(Double(revision)), "sha256": skill(revision)["sha256"],
                "context": .string("CONTEXTO PROPIO JARVIS V\(revision)"),
                "arguments": params["arguments"] ?? .object([:]), "executed_code": .bool(false)])])
        case "skills.get": return .object(["skill": skill(revision), "text": .string(skillText(revision))])
        default: throw RPCFailure.invalidResponse
        }
    }
}

private actor ExtensionReadGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private var entered = false
    private var released = false
    func hold() async {
        entered = true
        guard !released, !Task.isCancelled else { return }
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation = $0 }
        } onCancel: {
            Task { await self.release() }
        }
    }
    func waitUntilHeld() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while !entered {
            try Task.checkCancellation()
            guard ContinuousClock.now < deadline else { throw RPCFailure.unavailable }
            await Task.yield()
        }
    }
    func release() {
        released = true
        continuation?.resume(); continuation = nil
    }
}

private actor ExtensionReadWire {
    private var revision = 1
    private var gate: ExtensionReadGate?
    private var methods: [String] = []
    func holdNext(_ gate: ExtensionReadGate) { self.gate = gate }
    func update() { revision = 2 }
    func requests() -> [String] { methods }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        methods.append(method)
        let response = try ExtensionReadFixture.response(method, revision: revision, params: params)
        if let gate { self.gate = nil; await gate.hold() }
        return response
    }
}

final class ExtensionSelectionRaceTests: XCTestCase {
    @MainActor private func withHeldReply<Value: Sendable>(
        _ gate: ExtensionReadGate, pending: Task<Value, Never>,
        change: @MainActor () async throws -> Void
    ) async throws -> Value {
        do {
            try await gate.waitUntilHeld()
            try Task.checkCancellation()
            try await change()
        } catch {
            pending.cancel()
            await gate.release()
            _ = await pending.value
            throw error
        }
        await gate.release()
        return await pending.value
    }

    @MainActor func testPluginReadsCannotRepopulateOldVersionAfterUpdate() async throws {
        for method in ["plugins.resource", "plugins.catalog", "plugins.versions"] {
            let store = PluginStore(), wire = ExtensionReadWire(), gate = ExtensionReadGate()
            let old = try PluginDescriptor(ExtensionReadFixture.plugin(1))
            let current = try PluginDescriptor(ExtensionReadFixture.plugin(2))
            store.configure(PluginManagementClient(request: { try await wire.request($0, $1) }), runtimeIsActive: { true })
            store.plugins = [old]
            await wire.holdNext(gate)
            let pending = Task { @MainActor in await self.readPlugin(method, store: store, plugin: old) }
            _ = try await withHeldReply(gate, pending: pending) {
                store.plugins = [current]
                await wire.update()
            }
            XCTAssertNil(store.resource, method)
            XCTAssertTrue(store.catalogs.isEmpty, method)
            XCTAssertTrue(store.versions.isEmpty, method)
            await readPlugin(method, store: store, plugin: current)
            switch method {
            case "plugins.resource": XCTAssertEqual(store.resource?.text, "RECURSO PROPIO JARVIS V2")
            case "plugins.catalog": XCTAssertNotNil(store.catalogs[current.id])
            default: XCTAssertEqual(store.versions[current.id]?.first?.version, "2.0.0")
            }
            let requests = await wire.requests()
            XCTAssertEqual(requests, [method, method])
            await store.close()
        }
    }

    @MainActor func testPromptReplyCannotBeAddedAfterAgentSelectionChanges() async throws {
        let store = PluginStore(), wire = ExtensionReadWire(), gate = ExtensionReadGate()
        let plugin = try PluginDescriptor(ExtensionReadFixture.plugin(1))
        store.configure(PluginManagementClient(request: { try await wire.request($0, $1) }), runtimeIsActive: { true })
        store.plugins = [plugin]
        await wire.holdNext(gate)
        let pending = Task { @MainActor in await store.promptContext(plugin, path: "data.txt") }
        let stale = try await withHeldReply(gate, pending: pending) {
            store.selectAgent("otro")
        }
        XCTAssertNil(stale)
        store.selectAgent("jarvis"); store.plugins = [plugin]
        let fresh = await store.promptContext(plugin, path: "data.txt")
        XCTAssertTrue(fresh?.contains("RECURSO PROPIO JARVIS V1") == true)
        let requests = await wire.requests()
        XCTAssertEqual(requests, ["plugins.resource", "plugins.resource"])
        await store.close()
    }

    @MainActor func testSkillRepliesAreDiscardedAfterAgentSelectionChanges() async throws {
        for method in ["skills.metadata", "skills.versions", "skills.resource", "skills.invoke", "skills.get"] {
            let store = ManagementStore(), wire = ExtensionReadWire(), gate = ExtensionReadGate()
            let skill = try SkillDescriptor(ExtensionReadFixture.skill(1))
            store.configure(ManagementClient(request: { try await wire.request($0, $1) }), runtimeIsActive: { true })
            store.skills = [skill]
            await wire.holdNext(gate)
            let pending = Task { @MainActor in await self.readSkill(method, store: store, skill: skill) }
            let stale = try await withHeldReply(gate, pending: pending) {
                store.selectedAgentID = "otro"
            }
            XCTAssertNil(stale, method)
            XCTAssertNil(store.errorMessage, method)
            store.selectedAgentID = "jarvis"
            let fresh = await readSkill(method, store: store, skill: skill)
            XCTAssertNotNil(fresh, method)
            let requests = await wire.requests()
            XCTAssertEqual(requests, [method, method])
            await store.close()
        }
    }

    @MainActor func testOldSkillResourceAndInvocationCannotSurviveVersionReplacement() async throws {
        for method in ["skills.resource", "skills.invoke"] {
            let store = ManagementStore(), wire = ExtensionReadWire(), gate = ExtensionReadGate()
            let old = try SkillDescriptor(ExtensionReadFixture.skill(1))
            let current = try SkillDescriptor(ExtensionReadFixture.skill(2))
            store.configure(ManagementClient(request: { try await wire.request($0, $1) }), runtimeIsActive: { true })
            store.skills = [old]
            await wire.holdNext(gate)
            let pending = Task { @MainActor in await self.readSkill(method, store: store, skill: old) }
            let stale = try await withHeldReply(gate, pending: pending) {
                store.skills = [current]
                await wire.update()
            }
            XCTAssertNil(stale, method)
            let fresh = await readSkill(method, store: store, skill: current)
            XCTAssertTrue(fresh?.contains("V2") == true, method)
            let requests = await wire.requests()
            XCTAssertEqual(requests, [method, method])
            await store.close()
        }
    }

    @MainActor private func readPlugin(_ method: String, store: PluginStore, plugin: PluginDescriptor) async {
        switch method {
        case "plugins.resource": await store.readResource(plugin, path: "data.txt")
        case "plugins.catalog": await store.loadCatalog(plugin)
        default: await store.loadVersions(plugin)
        }
    }
    @MainActor private func readSkill(_ method: String, store: ManagementStore, skill: SkillDescriptor) async -> String? {
        switch method {
        case "skills.metadata": return await store.metadata(skill)?.version
        case "skills.versions": return await store.versions(skill)?.first?.version
        case "skills.resource": return await store.resource(skill, path: "data.txt")?.text
        case "skills.invoke": return await store.invoke(skill, arguments: [:])?.quotedDraft
        default: return await store.skillText(skill)
        }
    }
}
