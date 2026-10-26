import Foundation

/// These RPCs prepare metadata or submit one exact review. No generic approve,
/// automatic retry, shell command, profile admission or provider call exists.
public final class RecoveryExtensionClient: Sendable {
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
    private func read(_ method: String, kind: String? = nil, _ params: [String: RPCValue]) async throws -> MaintenanceOperation {
        try runtime.check(); let value = try await MaintenanceOperation(request(method, params)); try runtime.check()
        guard value.kind == (kind ?? method), RecoveryExtensionValidation.reads.contains(value.kind) else { throw RPCFailure.invalidResponse }; return value
    }
    private func target(_ parent: String, _ name: String) throws {
        guard MaintenanceValidation.path(parent), MaintenanceValidation.name(name) else { throw RPCFailure.configuration }
    }
    public func workspaceBackup(entries: [RecoveryWorkspaceEntry], parent: String, name: String) async throws -> MaintenanceOperation {
        try target(parent, name)
        guard !entries.isEmpty, entries.count <= 256, Set(entries.map(\.path)).count == entries.count else { throw RPCFailure.configuration }
        return try await read("workspace.backup.review", ["entries": .array(entries.map(\.wire)), "destination_parent": .string(parent), "name": .string(name)])
    }
    public func workspaceRestore(archive: String, expectedHash: String?, parent: String, name: String) async throws -> MaintenanceOperation {
        try target(parent, name)
        guard MaintenanceValidation.path(archive), expectedHash == nil || ManagementValidation.hash(expectedHash!) else { throw RPCFailure.configuration }
        var params: [String: RPCValue] = ["archive_path": .string(archive), "destination_parent": .string(parent), "name": .string(name)]
        if let expectedHash { params["expected_manifest_sha256"] = .string(expectedHash) }
        return try await read("workspace.restore.review", params)
    }
    public func repair(role: String, kind: String, parent: String, name: String) async throws -> MaintenanceOperation {
        try target(parent, name)
        guard ["state", "memory", "schedules"].contains(role), ["wal_valid_prefix", "memory_fts_rebuild"].contains(kind), kind != "memory_fts_rebuild" || role == "memory" else { throw RPCFailure.configuration }
        return try await read("doctor.repair.review", ["role": .string(role), "repair_kind": .string(kind), "destination_parent": .string(parent), "name": .string(name)])
    }
    public func release(artifact: String, installation: String, verificationID: String) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(artifact), MaintenanceValidation.path(installation), ManagementValidation.identifier(verificationID) else { throw RPCFailure.configuration }
        return try await read("maintenance.release.review", kind: "release.review", ["artifact_path": .string(artifact), "install_root": .string(installation), "verification_id": .string(verificationID)])
    }
    public func rollback(installation: String, verificationID: String) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(installation), ManagementValidation.identifier(verificationID) else { throw RPCFailure.configuration }
        return try await read("maintenance.release.rollback.review", kind: "release.rollback.review", ["install_root": .string(installation), "verification_id": .string(verificationID)])
    }
    public func inspect(installation: String) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(installation) else { throw RPCFailure.configuration }
        return try await read("maintenance.release.inspect", kind: "release.inspect", ["install_root": .string(installation)])
    }
    public func launchPlan(installation: String, verificationID: String, expectedCurrent: String?) async throws -> MaintenanceOperation {
        guard MaintenanceValidation.path(installation), ManagementValidation.identifier(verificationID), expectedCurrent == nil || ManagementValidation.hash(expectedCurrent!) else { throw RPCFailure.configuration }
        var params: [String: RPCValue] = ["install_root": .string(installation), "verification_id": .string(verificationID)]
        if let expectedCurrent { params["expected_current_sha256"] = .string(expectedCurrent) }
        return try await read("maintenance.release.resolve_launch", kind: "release.resolve_launch", params)
    }
    public func submit(_ review: RecoveryExtensionReview, now: Date = Date()) async throws -> MaintenanceOperation {
        guard review.expiresAt > now else { throw RPCFailure.configuration }; try runtime.check()
        let method = review.kind.hasPrefix("release.") ? "maintenance." + review.kind.replacingOccurrences(of: "update.", with: "") : review.kind
        let value = try await MaintenanceOperation(request(method, review.params)); try runtime.check()
        try review.validateResult(value); return value
    }
    public func decide(_ ticket: MaintenanceApproval, review: RecoveryExtensionReview, operation: MaintenanceOperation, allow: Bool) async throws -> Bool {
        guard review.matches(ticket, operation: operation) else { throw RPCFailure.configuration }; try runtime.check()
        var params = ticket.params; params["allow"] = .bool(allow)
        let value = try await request("maintenance.decide", params); try runtime.check()
        try MaintenanceValidation.keys(value, ["accepted"])
        guard let accepted = value["accepted"].bool else { throw RPCFailure.invalidResponse }; return accepted
    }
    public func cancel(_ operation: MaintenanceOperation) async throws -> MaintenanceOperation {
        try runtime.check(); let row = try await maintenance.cancel(operation.id); try runtime.check()
        guard row.follows(operation) else { throw RPCFailure.invalidResponse }; return row
    }
    public func cleanup(_ operation: MaintenanceOperation) async throws -> MaintenanceOperation {
        guard operation.cleanupOwnerPending, !operation.workerPending else { throw RPCFailure.configuration }; try runtime.check()
        let row = try await MaintenanceOperation(request("maintenance.cleanup", ["operation_id": .string(operation.id)])); try runtime.check()
        guard row.follows(operation) else { throw RPCFailure.invalidResponse }; return row
    }
}
