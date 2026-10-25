import Foundation

/// Explicit host controls. No account lookup, approval, opening or retries occur on reads.
public final class RestoreProfileClient: Sendable {
    public typealias Request = MaintenanceClient.Request
    private let request: Request
    private let maintenance: MaintenanceClient
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) }; maintenance = MaintenanceClient(transport: transport) }
    public init(request: @escaping Request) { self.request = request; maintenance = MaintenanceClient(request: request) }
    public func close() async { await maintenance.close() }
    public func status() async throws -> MaintenanceStatus { try await maintenance.status() }
    public func pending() async throws -> MaintenancePending { try await maintenance.pending() }
    public func operation(_ id: String) async throws -> MaintenanceOperation { try await maintenance.operation(id) }
    public func localStatus() async throws -> LocalProfileStatus { try await LocalProfileStatus(request("service.local_status", [:])) }
    public func review(path: String) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(path) else { throw RPCFailure.configuration }
        let value = try await MaintenanceOperation(request("profile.restore.review", ["profile_path": .string(path)]))
        guard value.kind == "profile.restore.review" else { throw RPCFailure.invalidResponse }; return value
    }
    public func activate(_ review: RestoreProfileReview, now: Date = Date()) async throws -> MaintenanceOperation {
        guard review.expiresAt > now else { throw RPCFailure.configuration }
        let value = try await MaintenanceOperation(request("profile.restore.activate", review.params))
        guard review.matches(value) else { throw RPCFailure.invalidResponse }; return value
    }
    public func inspect(path: String, marker: String? = nil, admission: String? = nil) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(path), marker == nil || ManagementValidation.hash(marker!), admission == nil || ManagementValidation.hash(admission!) else { throw RPCFailure.configuration }
        var params: [String: RPCValue] = ["profile_path": .string(path)]
        if let marker { params["expected_marker_sha256"] = .string(marker) }
        if let admission { params["expected_admission_sha256"] = .string(admission) }
        let value = try await MaintenanceOperation(request("profile.restore.inspect", params))
        guard value.kind == "profile.restore.inspect" else { throw RPCFailure.invalidResponse }; return value
    }
    public func decide(_ approval: MaintenanceApproval, review: RestoreProfileReview, operation: MaintenanceOperation, allow: Bool) async throws -> Bool {
        guard operation.phase == "awaiting_owner", !operation.settled, review.matches(approval, operation: operation) else { throw RPCFailure.configuration }
        var params = approval.params; params["allow"] = .bool(allow)
        let result = try await request("maintenance.decide", params)
        try MaintenanceValidation.keys(result, ["accepted"])
        guard let accepted = result["accepted"].bool else { throw RPCFailure.invalidResponse }; return accepted
    }
    public func cancel(_ id: String) async throws -> MaintenanceOperation { try await maintenance.cancel(id) }
    public func cleanup(_ id: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(id) else { throw RPCFailure.configuration }
        let row = try await MaintenanceOperation(request("maintenance.cleanup", ["operation_id": .string(id)]))
        guard row.id == id else { throw RPCFailure.invalidResponse }; return row
    }
}
