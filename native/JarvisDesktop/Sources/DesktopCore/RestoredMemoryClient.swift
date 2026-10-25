import Foundation

/// Explicit local host controls. Reads never query a provider, approve or replay.
public final class RestoredMemoryClient: Sendable {
    private let request: MaintenanceClient.Request
    private let maintenance: MaintenanceClient
    private let runtime: ProfileRuntimeGuard
    public init(transport: LocalRPCClient, runtime: ProfileRuntimeGuard) {
        request = { try await transport.call($0, params: $1) }; maintenance = MaintenanceClient(transport: transport); self.runtime = runtime
    }
    public init(runtime: ProfileRuntimeGuard, request: @escaping MaintenanceClient.Request) {
        self.request = request; maintenance = MaintenanceClient(request: request); self.runtime = runtime
    }
    public func close() async { await maintenance.close() }
    public func status() async throws -> MaintenanceStatus { try runtime.check(); let value = try await maintenance.status(); try runtime.check(); return value }
    public func pending() async throws -> MaintenancePending { try runtime.check(); let value = try await maintenance.pending(); try runtime.check(); return value }
    public func operation(_ id: String) async throws -> MaintenanceOperation { try runtime.check(); let value = try await maintenance.operation(id); try runtime.check(); return value }
    private func read(_ method: String, _ params: [String: RPCValue]) async throws -> MaintenanceOperation {
        try runtime.check(); let value = try await MaintenanceOperation(request(method, params)); try runtime.check()
        guard value.kind == method else { throw RPCFailure.invalidResponse }; return value
    }
    public func inventory(agentID: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(agentID, agent: true) else { throw RPCFailure.configuration }
        return try await read("memory.restore.inventory", ["agent_id": .string(agentID)])
    }
    public func page(inventoryID: String, cursor: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(inventoryID), ManagementValidation.identifier(cursor) else { throw RPCFailure.configuration }
        return try await read("memory.restore.inventory", ["inventory_id": .string(inventoryID), "cursor": .string(cursor)])
    }
    public func review(inventoryID: String, sourceHandle: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(inventoryID), ManagementValidation.identifier(sourceHandle) else { throw RPCFailure.configuration }
        return try await read("memory.restore.review", ["inventory_id": .string(inventoryID), "source_handle": .string(sourceHandle)])
    }
    public func submit(_ review: RestoredMemoryReview, now: Date = Date()) async throws -> MaintenanceOperation {
        guard review.expiresAt > now else { throw RPCFailure.configuration }; try runtime.check()
        let value = try await MaintenanceOperation(request(RestoredMemoryValidation.effect, review.params)); try runtime.check()
        guard review.matches(value) else { throw RPCFailure.invalidResponse }; return value
    }
    public func decide(_ approval: MaintenanceApproval, review: RestoredMemoryReview, operation: MaintenanceOperation, allow: Bool) async throws -> Bool {
        guard review.matches(approval, operation: operation) else { throw RPCFailure.configuration }; try runtime.check()
        var params = approval.params; params["allow"] = .bool(allow)
        let value = try await request("maintenance.decide", params); try runtime.check()
        try MaintenanceValidation.keys(value, ["accepted"])
        guard let accepted = value["accepted"].bool else { throw RPCFailure.invalidResponse }; return accepted
    }
    public func observation(operationID: String, reviewDigest: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(operationID), ManagementValidation.hash(reviewDigest) else { throw RPCFailure.configuration }
        return try await read("memory.restore.receipt", ["operation_id": .string(operationID), "review_digest": .string(reviewDigest)])
    }
    public func cancel(_ id: String) async throws -> MaintenanceOperation { try runtime.check(); let row = try await maintenance.cancel(id); try runtime.check(); return row }
    public func cleanup(_ id: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(id) else { throw RPCFailure.configuration }; try runtime.check()
        let row = try await MaintenanceOperation(request("maintenance.cleanup", ["operation_id": .string(id)])); try runtime.check()
        guard row.id == id else { throw RPCFailure.invalidResponse }; return row
    }
}
