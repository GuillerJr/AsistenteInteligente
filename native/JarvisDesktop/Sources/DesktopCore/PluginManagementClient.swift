import Foundation

public final class PluginManagementClient: Sendable {
    private let transport: LocalRPCClient
    public init(transport: LocalRPCClient) { self.transport = transport }
    public func close() async { await transport.close() }
    public func list(agent: String) async throws -> [PluginDescriptor] {
        let result = try await transport.call("plugins.list", params: ["agent_id": .string(agent)])
        let rows = try ManagementValidation.rows(result["plugins"], limit: 32, parse: PluginDescriptor.init)
        guard rows.allSatisfy({ $0.agentID == agent }), Set(rows.map(\.id)).count == rows.count else { throw RPCFailure.invalidResponse }
        return rows
    }
    public func inspect(path: String) async throws -> PluginPackagePreview {
        let result = try await transport.call("plugins.inspect", params: ["path": .string(path)])
        return try PluginPackagePreview(result["package"])
    }
    public func review(action: String, agent: String, fields: [String: RPCValue]) async throws -> PluginReview {
        guard ["install", "update", "rollback", "enable", "disable", "remove", "retire_history"].contains(action) else { throw RPCFailure.configuration }
        var params = fields; params["action"] = .string(action); params["agent_id"] = .string(agent)
        let result = try await transport.call("plugins.review", params: params)
        return try PluginReview(result["review"], expectedAgent: agent, expectedAction: action)
    }
    public func decide(_ review: PluginReview, allow: Bool) async throws -> RPCValue {
        try await transport.call("plugins.decide", params: ["review_id": .string(review.id), "digest": .string(review.digest), "allow": .bool(allow)])
    }
    public func versions(_ plugin: PluginDescriptor) async throws -> [ExtensionVersion] {
        let result = try await transport.call("plugins.versions", params: ["agent_id": .string(plugin.agentID), "plugin_id": .string(plugin.id)])
        return try ManagementValidation.rows(result["versions"], limit: 3, parse: ExtensionVersion.init)
    }
    public func resource(_ plugin: PluginDescriptor, path: String) async throws -> ExtensionResource {
        var params = plugin.revisionParams; params["path"] = .string(path)
        let result = try await transport.call("plugins.resource", params: params)
        return try ExtensionResource(result["resource"], expectedPath: path)
    }
    public func catalog(_ plugin: PluginDescriptor) async throws -> RPCValue {
        let result = try await transport.call("plugins.catalog", params: plugin.revisionParams)
        let value = result["catalog"]
        _ = try ManagementValidation.rows(value["files"], limit: 512, parse: ExtensionFileDescriptor.init)
        guard value["skills"].array != nil, value["prompts"].array != nil, value["hooks"].array != nil,
              let mcp = value["mcp"].array, mcp.count <= 8,
              try JSONEncoder().encode(value).count <= 262144 else { throw RPCFailure.invalidResponse }
        return value
    }
    public func reviewMCP(_ plugin: PluginDescriptor, serverID: String) async throws -> RPCValue {
        var params = plugin.revisionParams; params["server_id"] = .string(serverID)
        let result = try await transport.call("plugins.mcp.review", params: params)
        let review = result["review"]
        guard let id = review["review_id"].string, ManagementValidation.identifier(id),
              let digest = review["digest"].string, ManagementValidation.hash(digest),
              review["plugin_origin"]["plugin_id"].string == plugin.id,
              review["plugin_origin"]["revision"].integer == plugin.revision,
              review["plugin_origin"]["sha256"].string == plugin.hash else { throw RPCFailure.invalidResponse }
        _ = try MCPConfiguration(spec: review["configuration"])
        return review
    }
    public func decideMCP(_ review: RPCValue, allow: Bool) async throws -> RPCValue {
        try await transport.call("plugins.mcp.decide", params: ["review_id": review["review_id"], "digest": review["digest"], "allow": .bool(allow)])
    }
    public func cleanup() async throws -> ExtensionCleanupResult { try await ExtensionCleanupResult(transport.call("extensions.cleanup")) }
    public func runHook(_ plugin: PluginDescriptor, hook: PluginHookDescriptor, scope: SessionScope, requestID: String) async throws -> RPCValue {
        var params = plugin.revisionParams
        params.merge(scope.params) { _, new in new }
        params.merge(["event": .string(hook.event), "hook_sha256": .string(hook.hash), "request_id": .string(requestID)]) { _, new in new }
        return try await transport.call("plugins.hooks.run", params: params)
    }
}
