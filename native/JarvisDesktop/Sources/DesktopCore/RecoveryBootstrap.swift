import Foundation

/// Session/schedule enumeration opens SQLite and can consume the WAL/journal
/// under recovery. This local UI mode is neither a sandbox nor an RPC grant.
public enum RecoveryBootstrap {
    public static func inspect(request: MaintenanceClient.Request) async throws -> LocalProfileStatus {
        let health = try await request("health", [:])
        guard health["service"].string == "jarvis",
              health["protocol"].string == "jarvis-local-rpc",
              health["version"].integer == 1 else { throw RPCFailure.invalidResponse }
        return try await LocalProfileStatus(request("service.local_status", [:]))
    }
}
