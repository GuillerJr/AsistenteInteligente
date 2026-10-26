import Foundation

enum ChannelReconciliationValidation {
    static let effect = "telegram.restore.reconcile"
    static let noEffects: Set<String> = ["network_started", "provider_started", "grants_restored", "historical_events_replayed"]
    static func identity(_ value: RPCValue) throws -> Int {
        guard let number = TelegramValidation.integer(value, minimum: 1) else { throw RPCFailure.invalidResponse }; return number
    }
    static func hash(_ value: RPCValue) throws -> String { try RestoredMemoryValidation.hash(value) }
}

/// A local route admission. It never represents pairing with Telegram or permission to start polling.
public struct ChannelReconciliationReview: Identifiable, Sendable {
    public let id: String
    public let digest: String
    public let scope: MaintenanceScope
    public let botID: Int
    public let chatID: Int
    public let agentID: String
    public let sessionID: String
    public let revision: Int
    public let enabledBefore: Bool
    public let enabledAfter: Bool
    public let workspacePath: String
    public let workspaceID: String
    public let metadata: RPCValue
    public let expiresAt: Date
    public var params: [String: RPCValue] { ["review_id": .string(id), "review_digest": .string(digest)] }
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "expires_in_seconds", "metadata"])
        id = try RestoredMemoryValidation.id(value["review_id"]); digest = try ChannelReconciliationValidation.hash(value["review_digest"])
        let row = value["metadata"]
        try MaintenanceValidation.keys(row, ["profile_id", "generation", "bot_id", "chat_id", "agent_id", "agent_revision", "session_id", "route_revision", "enabled_before", "enabled_after", "workspace_id", "workspace_path", "configuration_hash", "marker_sha256", "admission_sha256", "network_started", "provider_started", "grants_restored", "historical_events_replayed", "external_bot_verified"])
        guard ManagementValidation.digest(try MaintenanceValidation.canonical(row)) == digest,
              ChannelReconciliationValidation.noEffects.union(["external_bot_verified"]).allSatisfy({ row[$0] == .bool(false) }) else { throw RPCFailure.invalidResponse }
        scope = try MaintenanceScope(row); botID = try ChannelReconciliationValidation.identity(row["bot_id"])
        chatID = try ChannelReconciliationValidation.identity(row["chat_id"])
        agentID = try RestoredMemoryValidation.id(row["agent_id"], agent: true); sessionID = try RestoredMemoryValidation.id(row["session_id"])
        guard try MaintenanceValidation.integer(row["agent_revision"], max: Int.max) > 0 else { throw RPCFailure.invalidResponse }
        revision = try MaintenanceValidation.integer(row["route_revision"], max: Int.max)
        enabledBefore = try RestoredMemoryValidation.bool(row["enabled_before"]); enabledAfter = try RestoredMemoryValidation.bool(row["enabled_after"])
        guard revision > 0, enabledBefore != enabledAfter else { throw RPCFailure.invalidResponse }
        workspacePath = try RestoredMemoryValidation.path(row["workspace_path"]); workspaceID = try ChannelReconciliationValidation.hash(row["workspace_id"])
        for key in ["configuration_hash", "marker_sha256", "admission_sha256"] { _ = try ChannelReconciliationValidation.hash(row[key]) }
        let ttl = try MaintenanceValidation.integer(value["expires_in_seconds"], max: 120)
        guard ttl > 0 else { throw RPCFailure.invalidResponse }
        expiresAt = now.addingTimeInterval(Double(ttl)); metadata = row
    }
    public func matches(_ operation: MaintenanceOperation) -> Bool {
        guard operation.kind == ChannelReconciliationValidation.effect, operation.scope == scope, operation.reviewID == id, operation.reviewDigest == digest else { return false }
        guard let receipt = operation.channelReceipt else { return true }
        return receipt.requestDigest == digest && receipt.botID == botID && receipt.chatID == chatID && receipt.revisionBefore == revision && receipt.enabledBefore == enabledBefore && receipt.enabledAfter == enabledAfter
    }
    public func matches(_ ticket: MaintenanceApproval, operation: MaintenanceOperation, now: Date = Date()) -> Bool {
        matches(operation) && operation.phase == "awaiting_owner" && ticket.operationID == operation.id && ticket.purpose == ChannelReconciliationValidation.effect && ticket.scope == scope && ticket.reviewID == id && ticket.reviewDigest == digest && ticket.reviewMetadata == metadata && expiresAt > now && ticket.expiresAt > now
    }
}

public struct ChannelReconciliationReceipt: Sendable {
    public let requestDigest: String
    public let status: String
    public let botID: Int
    public let chatID: Int
    public let revisionBefore: Int
    public let revisionAfter: Int?
    public let enabledBefore: Bool
    public let enabledAfter: Bool
    public init(_ value: RPCValue, operationID: String) throws {
        try MaintenanceValidation.keys(value, ["operation_id", "purpose", "request_digest", "status", "effect_status", "bot_id", "chat_id", "route_revision_before", "route_revision_after", "enabled_before", "enabled_after", "configuration_hash_before", "configuration_hash_after", "error_code", "cleanup_pending", "network_started", "provider_started", "grants_restored", "historical_events_replayed", "permits_replay"])
        guard value["operation_id"] == .string(operationID), value["purpose"] == .string(ChannelReconciliationValidation.effect),
              let status = value["status"].string, ["completed", "failed", "cancelled", "unknown"].contains(status),
              value["cleanup_pending"].bool != nil,
              status == "unknown" || value["cleanup_pending"] == .bool(false),
              ChannelReconciliationValidation.noEffects.union(["permits_replay"]).allSatisfy({ value[$0] == .bool(false) }) else { throw RPCFailure.invalidResponse }
        self.status = status; requestDigest = try ChannelReconciliationValidation.hash(value["request_digest"])
        botID = try ChannelReconciliationValidation.identity(value["bot_id"]); chatID = try ChannelReconciliationValidation.identity(value["chat_id"])
        revisionBefore = try MaintenanceValidation.integer(value["route_revision_before"], max: Int.max)
        revisionAfter = value["route_revision_after"] == .null ? nil : try MaintenanceValidation.integer(value["route_revision_after"], max: Int.max)
        enabledBefore = try RestoredMemoryValidation.bool(value["enabled_before"]); enabledAfter = try RestoredMemoryValidation.bool(value["enabled_after"])
        guard revisionBefore > 0, enabledBefore != enabledAfter else { throw RPCFailure.invalidResponse }
        _ = try ChannelReconciliationValidation.hash(value["configuration_hash_before"])
        let after = value["configuration_hash_after"] == .null ? nil : try ChannelReconciliationValidation.hash(value["configuration_hash_after"])
        let error = try MaintenanceValidation.code(value["error_code"])
        if status == "completed" {
            guard value["effect_status"] == .string("route_reconciled"), value["cleanup_pending"] == .bool(false), revisionBefore < Int.max, revisionAfter == revisionBefore + 1, after != nil, error == nil else { throw RPCFailure.invalidResponse }
        } else if status == "unknown" {
            guard value["effect_status"] == .string("route_change_unknown") else { throw RPCFailure.invalidResponse }
        } else {
            guard value["effect_status"] == .string("no_route_change_confirmed"), revisionAfter == nil, after == nil else { throw RPCFailure.invalidResponse }
        }
    }
}

public struct ChannelReconciliationObservation: Sendable {
    public let botID: Int
    public let route: TelegramRouteSnapshot
    public let workspaceID: String
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["observation_only", "permits_replay", "prior_acknowledgement", "bot_id", "route", "configuration_hash", "workspace_id", "network_started"])
        guard value["observation_only"] == .bool(true), value["permits_replay"] == .bool(false), value["prior_acknowledgement"] == .string("not_established"), value["network_started"] == .bool(false) else { throw RPCFailure.invalidResponse }
        botID = try ChannelReconciliationValidation.identity(value["bot_id"]); route = try TelegramRouteSnapshot(value["route"])
        workspaceID = try ChannelReconciliationValidation.hash(value["workspace_id"])
        _ = try ChannelReconciliationValidation.hash(value["configuration_hash"])
    }
}
