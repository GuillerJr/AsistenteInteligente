import Foundation

/// Host controls only: no inference, provider status call, automatic approval or replay.
public final class MaintenanceClient: Sendable {
    public typealias Request = @Sendable (String, [String: RPCValue]) async throws -> RPCValue
    private let request: Request
    private let finish: @Sendable () async -> Void
    public init(transport: LocalRPCClient) {
        request = { try await transport.call($0, params: $1) }; finish = { await transport.close() }
    }
    public init(request: @escaping Request, close: @escaping @Sendable () async -> Void = {}) { self.request = request; finish = close }
    public func close() async { await finish() }
    public func status() async throws -> MaintenanceStatus { try await MaintenanceStatus(request("maintenance.status", [:])) }
    public func pending() async throws -> MaintenancePending { try await MaintenancePending(request("maintenance.pending", [:])) }
    public func operation(_ id: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(id) else { throw RPCFailure.configuration }
        let row = try await MaintenanceOperation(request("maintenance.status", ["operation_id": .string(id)]))
        guard row.id == id else { throw RPCFailure.invalidResponse }; return row
    }
    public func reviewBackup(parent: String, name: String) async throws -> MaintenanceReview {
        try target(parent, name)
        let row = try await MaintenanceReview(request("backup.review", ["destination_parent": .string(parent), "name": .string(name)]))
        guard row.kind == "backup.create", row.destinationParent == parent, row.destinationName == name else { throw RPCFailure.invalidResponse }; return row
    }
    public func reviewRestore(verificationID: String, parent: String, name: String) async throws -> MaintenanceReview {
        try target(parent, name)
        guard ManagementValidation.identifier(verificationID) else { throw RPCFailure.configuration }
        let row = try await MaintenanceReview(request("restore.review", ["verification_id": .string(verificationID), "destination_parent": .string(parent), "name": .string(name)]))
        guard row.kind == "restore.stage", row.destinationParent == parent, row.destinationName == name else { throw RPCFailure.invalidResponse }; return row
    }
    public func submit(_ review: MaintenanceReview, now: Date = Date()) async throws -> MaintenanceOperation {
        guard review.expiresAt > now, review.kind != "backup.create" || !review.sources.contains(where: { $0.disposition == "unavailable" }) else { throw RPCFailure.configuration }
        let row = try await MaintenanceOperation(request(review.kind, review.params))
        guard row.kind == review.kind else { throw RPCFailure.invalidResponse }; return row
    }
    public func verify(path: String, expectedHash: String?) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(path), expectedHash == nil || ManagementValidation.hash(expectedHash!) else { throw RPCFailure.configuration }
        var params: [String: RPCValue] = ["backup_path": .string(path)]
        if let expectedHash { params["expected_manifest_sha256"] = .string(expectedHash) }
        let row = try await MaintenanceOperation(request("backup.verify", params))
        guard row.kind == "backup.verify" else { throw RPCFailure.invalidResponse }; return row
    }
    public func doctor() async throws -> MaintenanceOperation {
        let row = try await MaintenanceOperation(request("doctor.report", [:]))
        guard row.kind == "doctor.report" else { throw RPCFailure.invalidResponse }; return row
    }
    public func decide(_ approval: MaintenanceApproval, review: MaintenanceReview, operation: MaintenanceOperation, allow: Bool, now: Date = Date()) async throws -> Bool {
        guard !operation.settled, operation.phase == "awaiting_owner", approval.matches(review, operation: operation, now: now) else { throw RPCFailure.configuration }
        var params = approval.params; params["allow"] = .bool(allow)
        let value = try await request("maintenance.decide", params)
        try MaintenanceValidation.keys(value, ["accepted"])
        guard let accepted = value["accepted"].bool else { throw RPCFailure.invalidResponse }; return accepted
    }
    public func cancel(_ id: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(id) else { throw RPCFailure.configuration }
        let row = try await MaintenanceOperation(request("maintenance.cancel", ["operation_id": .string(id)]))
        guard row.id == id else { throw RPCFailure.invalidResponse }; return row
    }
    private func target(_ parent: String, _ name: String) throws {
        guard MaintenanceValidation.path(parent), MaintenanceValidation.name(name) else { throw RPCFailure.configuration }
    }
}
