import Foundation

public struct MCPRelaySnapshot: Equatable, Sendable {
    public let running: Bool
    public let endpoint: String?
    public let exportCount: Int
    public let remoteClients: Int
    public let tokenAvailable: Bool
    public let requiresReview: Bool
    public init(_ value: RPCValue) throws {
        guard let running = value["running"].bool,
              let count = value["export_count"].integer, (0...128).contains(count),
              let clients = value["remote_clients"].integer, (0...64).contains(clients),
              let token = value["token_available"].bool else { throw RPCFailure.invalidResponse }
        if case .null = value["endpoint"] { endpoint = nil }
        else if let text = value["endpoint"].string, let url = URLComponents(string: text),
                url.scheme == "http", url.host == "127.0.0.1", url.port.map({ (1...65535).contains($0) }) == true,
                url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
                ManagementValidation.text(text, limit: 4096) { endpoint = text }
        else { throw RPCFailure.invalidResponse }
        guard !running || endpoint != nil else { throw RPCFailure.invalidResponse }
        if let value = value.object?["requires_review"] {
            guard let flag = value.bool else { throw RPCFailure.invalidResponse }
            requiresReview = flag
        } else { requiresReview = false }
        guard !requiresReview || !token else { throw RPCFailure.invalidResponse }
        self.running = running; exportCount = count; remoteClients = clients; tokenAvailable = token
    }
}
public struct MCPRelayReview: Equatable, Sendable {
    public let id: String
    public let digest: String
    public let value: RPCValue
    public let expiresAt: Date
    public init(_ value: RPCValue) throws {
        guard let id = value["review_id"].string, ManagementValidation.identifier(id),
              let digest = value["digest"].string, ManagementValidation.hash(digest),
              value["address"].string == "127.0.0.1", case .number(let expires) = value["expires_ns"], expires.isFinite, expires > 0,
              try JSONEncoder().encode(value).count <= 65536 else { throw RPCFailure.invalidResponse }
        self.id = id; self.digest = digest; self.value = value; expiresAt = Date(timeIntervalSince1970: expires / 1_000_000_000)
    }
}
public extension MCPManagementClient {
    func relayStatus() async throws -> MCPRelaySnapshot {
        let result = try await call("mcp.relay.status")
        return try MCPRelaySnapshot(result["relay"])
    }
    func reviewRelay(scope: SessionScope, action: String, exports: [String]) async throws -> MCPRelayReview {
        guard ["start", "stop", "revoke"].contains(action), exports.count <= 128 else { throw RPCFailure.configuration }
        var params = scope.params
        params.merge(["action": .string(action), "exports": .array(exports.map(RPCValue.string)), "port": .number(0)]) { _, new in new }
        let result = try await call("mcp.relay.review", params: params)
        return try MCPRelayReview(result["review"])
    }
    func decideRelay(_ review: MCPRelayReview, allow: Bool) async throws -> RPCValue {
        try await call("mcp.relay.decide", params: ["review_id": .string(review.id), "digest": .string(review.digest), "allow": .bool(allow)])
    }
    func relayToken() async throws -> String {
        let result = try await call("mcp.relay.token")
        guard let token = result["token"].string, (32...256).contains(token.utf8.count),
              token.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || "_-".unicodeScalars.contains($0) }) else { throw RPCFailure.invalidResponse }
        return token
    }
}
