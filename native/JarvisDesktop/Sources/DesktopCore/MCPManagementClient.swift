import Foundation

/// Read-only listing and configuration do not launch a process. Only connect
/// sends the explicit command/version reviewed by the owner to the host.
public final class MCPManagementClient: Sendable {
    public typealias Request = @Sendable (String, [String: RPCValue]) async throws -> RPCValue
    private let request: Request
    private let finish: @Sendable () async -> Void
    public init(transport: LocalRPCClient) {
        request = { try await transport.call($0, params: $1) }; finish = { await transport.close() }
    }
    public init(request: @escaping Request, close: @escaping @Sendable () async -> Void = {}) {
        self.request = request; finish = close
    }
    public func close() async { await finish() }
    func call(_ method: String, params: [String: RPCValue] = [:]) async throws -> RPCValue {
        try await request(method, params)
    }

    public func list() async throws -> MCPCatalog {
        let result = try await call("mcp.list")
        return try MCPCatalog(result)
    }

    public func add(_ configuration: MCPConfiguration) async throws {
        let result = try await call("mcp.add", params: configuration.params)
        guard result["configured"].bool == true else { throw RPCFailure.invalidResponse }
    }

    public func connect(_ configuration: MCPConfiguration) async throws -> MCPServerSnapshot {
        let result = try await call("mcp.connect", params: configuration.params)
        let server = try MCPServerSnapshot(result["server"])
        guard server.configuration == configuration else { throw RPCFailure.invalidResponse }
        return server
    }

    public func disconnect(_ serverID: String) async throws {
        let result = try await call("mcp.disconnect", params: ["server_id": .string(serverID)])
        guard result["disconnected"].bool == true else { throw RPCFailure.invalidResponse }
    }

    public func remove(_ serverID: String) async throws {
        let result = try await call("mcp.remove", params: ["server_id": .string(serverID)])
        guard result["removed"].bool == true else { throw RPCFailure.invalidResponse }
    }
}
