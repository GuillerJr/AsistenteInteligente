import Foundation

public extension MCPManagementClient {
    func catalog(serverID: String) async throws -> MCPContentCatalog {
        try await MCPContentCatalog(call("mcp.catalog", params: ["server_id": .string(serverID)]), expectedServer: serverID)
    }
    func content(scope: SessionScope, serverID: String, catalog: String, kind: String,
                 name: String, arguments: [String: RPCValue] = [:], requestID: String) async throws -> RPCValue {
        guard ["resource", "prompt", "template"].contains(kind), ManagementValidation.identifier(requestID) else { throw RPCFailure.configuration }
        var params = scope.params
        let target = kind == "resource" ? "uri" : kind == "template" ? "template_uri" : "name"
        params.merge(["server_id": .string(serverID), "kind": .string(kind), "expected_catalog_hash": .string(catalog),
            "request_id": .string(requestID), "arguments": .object(arguments), target: .string(name)]) { _, new in new }
        return try await call("mcp.content.request", params: params)
    }
    func contentResult(scope: SessionScope, runID: String) async throws -> MCPContentResult {
        var params = scope.params; params["run_id"] = .string(runID)
        return try await MCPContentResult(call("mcp.content.result", params: params))
    }
    func openApp(scope: SessionScope, serverID: String, catalog: String, uri: String, requestID: String, toolRunID: String? = nil) async throws -> RPCValue {
        var params = scope.params
        params.merge(["server_id": .string(serverID), "resource_uri": .string(uri), "expected_catalog_hash": .string(catalog),
            "request_id": .string(requestID)]) { _, new in new }
        if let toolRunID { params["tool_run_id"] = .string(toolRunID) }
        return try await call("mcp.apps.open", params: params)
    }
    func appDocument(scope: SessionScope, runID: String) async throws -> RPCValue {
        var params = scope.params; params["run_id"] = .string(runID)
        return try await call("mcp.apps.document", params: params)
    }
    func appRequest(id: String, message: RPCValue) async throws -> RPCValue {
        try await call("mcp.apps.request", params: ["app_instance_id": .string(id), "message": message])
    }
    func pollApp(id: String, requestID: String) async throws -> RPCValue {
        try await call("mcp.apps.poll", params: ["app_instance_id": .string(id), "request_id": .string(requestID)])
    }
    func closeApp(id: String) async throws {
        let result = try await call("mcp.apps.close", params: ["app_instance_id": .string(id)])
        guard result["closed"].bool == true else { throw RPCFailure.invalidResponse }
    }
    func oauth(_ action: String, serverID: String, fields: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard ["discover", "begin", "finish", "refresh", "revoke", "status"].contains(action) else { throw RPCFailure.configuration }
        var params = fields; params["server_id"] = .string(serverID)
        return try await call("mcp.oauth." + action, params: params)
    }
    func expandTemplate(serverID: String, catalog: String, template: String, arguments: [String: RPCValue]) async throws -> String {
        let result = try await call("mcp.template.expand", params: ["server_id": .string(serverID), "expected_catalog_hash": .string(catalog),
            "template_uri": .string(template), "arguments": .object(arguments)])
        guard let uri = result["uri"].string, ManagementValidation.text(uri, limit: 4096) else { throw RPCFailure.invalidResponse }
        return uri
    }
}
