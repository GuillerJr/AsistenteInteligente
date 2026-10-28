import Foundation

/// Read-only listing and configuration do not launch a process. Only connect
/// sends the explicit command/version reviewed by the owner to the host.
public final class MCPManagementClient: Sendable {
    let transport: LocalRPCClient

    public init(transport: LocalRPCClient) { self.transport = transport }
    public func close() async { await transport.close() }

    public func list() async throws -> MCPCatalog {
        let result = try await transport.call("mcp.list")
        return try MCPCatalog(result)
    }

    public func add(_ configuration: MCPConfiguration) async throws {
        let result = try await transport.call("mcp.add", params: configuration.params)
        guard result["configured"].bool == true else { throw RPCFailure.invalidResponse }
    }

    public func connect(_ configuration: MCPConfiguration) async throws -> MCPServerSnapshot {
        let result = try await transport.call("mcp.connect", params: configuration.params)
        let server = try MCPServerSnapshot(result["server"])
        guard server.configuration == configuration else { throw RPCFailure.invalidResponse }
        return server
    }

    public func disconnect(_ serverID: String) async throws {
        let result = try await transport.call("mcp.disconnect", params: ["server_id": .string(serverID)])
        guard result["disconnected"].bool == true else { throw RPCFailure.invalidResponse }
    }

    public func remove(_ serverID: String) async throws {
        let result = try await transport.call("mcp.remove", params: ["server_id": .string(serverID)])
        guard result["removed"].bool == true else { throw RPCFailure.invalidResponse }
    }
}
