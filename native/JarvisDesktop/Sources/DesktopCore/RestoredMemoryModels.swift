import Foundation

/// Metadata only. Historical scopes and note content never cross this API.
enum RestoredMemoryValidation {
    static let sourceKinds = Set(["owned_snapshot", "cold_delete_snapshot", "cold_wal_snapshot", "cold_journal_snapshot"])
    static let effect = "memory.restore.copy_scope"
    static let reads = Set(["memory.restore.inventory", "memory.restore.review", "memory.restore.receipt"])
    static let kinds = reads.union([effect])
    static func id(_ value: RPCValue, agent: Bool = false) throws -> String {
        guard let text = value.string, ManagementValidation.identifier(text, agent: agent) else { throw RPCFailure.invalidResponse }; return text
    }
    static func hash(_ value: RPCValue) throws -> String {
        guard let text = value.string, ManagementValidation.hash(text) else { throw RPCFailure.invalidResponse }; return text
    }
    static func decimal(_ value: RPCValue, max: UInt64 = 4_503_599_627_370_495, positive: Bool = false) throws -> String {
        guard let text = value.string, text.range(of: "^(0|[1-9][0-9]{0,19})\\z", options: .regularExpression) != nil,
              let number = UInt64(text), number <= max, !positive || number > 0 else { throw RPCFailure.invalidResponse }; return text
    }
    static func generation(_ value: RPCValue, present: Bool) throws -> String? {
        if !present { guard value == .null else { throw RPCFailure.invalidResponse }; return nil }
        return try decimal(value)
    }
    static func path(_ value: RPCValue) throws -> String {
        guard let text = value.string, MaintenanceValidation.path(text) else { throw RPCFailure.invalidResponse }; return text
    }
    static func bool(_ value: RPCValue) throws -> Bool { guard let result = value.bool else { throw RPCFailure.invalidResponse }; return result }
}

public struct RestoredMemorySource: Identifiable, Sendable, Equatable {
    public let id: String
    public let agentID: String
    public let generation: String
    public let documents: Int
    public let bytes: Int
    public let largestDocumentBytes: Int
    public let fingerprint: String
    public let eligible: Bool
    public let reason: String?
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["source_handle", "agent_id", "source_generation", "documents", "bytes", "largest_document_bytes", "fingerprint", "eligible", "reason"])
        id = try RestoredMemoryValidation.id(value["source_handle"]); agentID = try RestoredMemoryValidation.id(value["agent_id"], agent: true)
        generation = try RestoredMemoryValidation.decimal(value["source_generation"])
        documents = try MaintenanceValidation.integer(value["documents"], max: 100_000)
        bytes = try MaintenanceValidation.integer(value["bytes"], max: 268_435_456)
        largestDocumentBytes = try MaintenanceValidation.integer(value["largest_document_bytes"], max: 1_048_576)
        fingerprint = try RestoredMemoryValidation.hash(value["fingerprint"])
        eligible = try RestoredMemoryValidation.bool(value["eligible"]); reason = try MaintenanceValidation.code(value["reason"])
        guard documents > 0, bytes >= documents, largestDocumentBytes > 0, largestDocumentBytes <= bytes,
              eligible == (reason == nil) else { throw RPCFailure.invalidResponse }
    }
}

public struct RestoredMemoryInventory: Sendable {
    public let id: String
    public let scope: MaintenanceScope
    public let root: MaintenanceFileIdentity
    public let agentID: String
    public let agentRevision: String
    public let schemaVersion: Int?
    public let sourceKind: String
    public let workspacePath: String
    public let workspace: MaintenanceFileIdentity
    public let destinationEmpty: Bool
    public let destinationPresent: Bool
    public let destinationGeneration: String?
    public let sources: [RestoredMemorySource]
    public let candidateCount: Int
    public let excludedSessionScopes: Int
    public let nextCursor: String?
    public let expiresAt: Date
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["inventory_id", "expires_in_seconds", "scope", "root_identity", "agent_id", "agent_revision", "schema_version", "source_kind", "destination", "sources", "candidate_count", "session_bound_scopes_excluded", "next_cursor", "page_size", "provider_used", "source_workspace_resolved"])
        try MaintenanceValidation.keys(value["scope"], ["profile_id", "generation"])
        id = try RestoredMemoryValidation.id(value["inventory_id"]); scope = try MaintenanceScope(value["scope"])
        root = try MaintenanceFileIdentity(value["root_identity"]); agentID = try RestoredMemoryValidation.id(value["agent_id"], agent: true)
        agentRevision = try RestoredMemoryValidation.decimal(value["agent_revision"], max: UInt64(Int64.max), positive: true)
        if value["schema_version"] == .null { schemaVersion = nil } else {
            let version = try MaintenanceValidation.integer(value["schema_version"], max: 2)
            guard version == 1 || version == 2 else { throw RPCFailure.invalidResponse }; schemaVersion = version
        }
        guard let kind = value["source_kind"].string, RestoredMemoryValidation.sourceKinds.union(["absent"]).contains(kind),
              value["page_size"] == .number(32), value["provider_used"] == .bool(false), value["source_workspace_resolved"] == .bool(false) else { throw RPCFailure.invalidResponse }
        sourceKind = kind
        let destination = value["destination"]
        try MaintenanceValidation.keys(destination, ["workspace_path", "workspace_identity", "empty", "scope_present", "scope_generation"])
        workspacePath = try RestoredMemoryValidation.path(destination["workspace_path"]); workspace = try MaintenanceFileIdentity(destination["workspace_identity"])
        destinationEmpty = try RestoredMemoryValidation.bool(destination["empty"])
        destinationPresent = try RestoredMemoryValidation.bool(destination["scope_present"])
        destinationGeneration = try RestoredMemoryValidation.generation(destination["scope_generation"], present: destinationPresent)
        sources = try ManagementValidation.rows(value["sources"], limit: 32, parse: RestoredMemorySource.init)
        candidateCount = try MaintenanceValidation.integer(value["candidate_count"], max: 10_000)
        excludedSessionScopes = try MaintenanceValidation.integer(value["session_bound_scopes_excluded"], max: 100_000)
        nextCursor = value["next_cursor"] == .null ? nil : try RestoredMemoryValidation.id(value["next_cursor"])
        let expectedAgent = agentID
        guard Set(sources.map(\.id)).count == sources.count, sources.allSatisfy({ $0.agentID == expectedAgent }), sources.count <= candidateCount, nextCursor == nil || sources.count < candidateCount,
              (kind == "absent") == (schemaVersion == nil), kind != "absent" || (candidateCount == 0 && nextCursor == nil && !destinationPresent) else { throw RPCFailure.invalidResponse }
        let ttl = try MaintenanceValidation.integer(value["expires_in_seconds"], max: 120)
        expiresAt = now.addingTimeInterval(Double(ttl))
    }
    public func sameSnapshot(as other: Self) -> Bool {
        id == other.id && scope == other.scope && root == other.root && agentID == other.agentID && agentRevision == other.agentRevision && schemaVersion == other.schemaVersion && sourceKind == other.sourceKind && workspacePath == other.workspacePath && workspace == other.workspace && destinationEmpty == other.destinationEmpty && destinationPresent == other.destinationPresent && destinationGeneration == other.destinationGeneration && candidateCount == other.candidateCount && excludedSessionScopes == other.excludedSessionScopes
    }
}

public struct RestoredMemoryReview: Identifiable, Sendable {
    public let id: String
    public let digest: String
    public let coreDigest: String
    public let scope: MaintenanceScope
    public let root: MaintenanceFileIdentity
    public let agentID: String
    public let agentRevision: String
    public let inventoryID: String
    public let sourceHandle: String
    public let sourceFingerprint: String
    public let sourceGeneration: String
    public let documents: Int
    public let bytes: Int
    public let workspacePath: String
    public let workspace: MaintenanceFileIdentity
    public let schemaBefore: Int
    public let migrationRequired: Bool
    public let sourceKind: String
    public let coldSQLiteRecoveryRequired: Bool
    public let expiresAt: Date
    public let metadata: RPCValue
    public var params: [String: RPCValue] { ["review_id": .string(id), "review_digest": .string(digest)] }
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "expires_in_seconds", "metadata"])
        id = try RestoredMemoryValidation.id(value["review_id"]); digest = try RestoredMemoryValidation.hash(value["review_digest"])
        let row = value["metadata"]
        try MaintenanceValidation.keys(row, ["kind", "profile_id", "generation", "root_identity", "database_identity", "agent_id", "agent_revision", "inventory_id", "source_handle", "source_generation", "source_fingerprint", "source_documents", "source_bytes", "largest_document_bytes", "destination_workspace_path", "destination_workspace_identity", "destination_scope_present", "destination_generation", "destination_documents", "core_review_digest", "schema_before", "schema_after", "migration_required", "limits", "policy", "retains_source", "new_document_ids", "new_document_revision", "source_labels_verified", "uri_resources_opened", "provider_used", "grants_restored", "source_kind", "cold_sqlite_recovery_required"])
        guard row["kind"] == .string(RestoredMemoryValidation.effect), row["destination_documents"] == .number(0), row["schema_after"] == .number(2), row["policy"] == .string("same_agent_durable_scope_to_empty_current_workspace"), row["retains_source"] == .bool(true), row["new_document_ids"] == .bool(true), row["new_document_revision"] == .number(1), ["source_labels_verified", "uri_resources_opened", "provider_used", "grants_restored"].allSatisfy({ row[$0] == .bool(false) }), ManagementValidation.digest(try MaintenanceValidation.canonical(row)) == digest else { throw RPCFailure.invalidResponse }
        scope = try MaintenanceScope(row); root = try MaintenanceFileIdentity(row["root_identity"])
        _ = try MaintenanceFileIdentity(row["database_identity"])
        agentID = try RestoredMemoryValidation.id(row["agent_id"], agent: true)
        agentRevision = try RestoredMemoryValidation.decimal(row["agent_revision"], max: UInt64(Int64.max), positive: true)
        inventoryID = try RestoredMemoryValidation.id(row["inventory_id"]); sourceHandle = try RestoredMemoryValidation.id(row["source_handle"])
        sourceGeneration = try RestoredMemoryValidation.decimal(row["source_generation"]); sourceFingerprint = try RestoredMemoryValidation.hash(row["source_fingerprint"])
        documents = try MaintenanceValidation.integer(row["source_documents"], max: 100_000)
        bytes = try MaintenanceValidation.integer(row["source_bytes"], max: 268_435_456)
        let largest = try MaintenanceValidation.integer(row["largest_document_bytes"], max: 1_048_576)
        guard documents > 0, bytes >= documents, largest > 0, largest <= bytes else { throw RPCFailure.invalidResponse }
        workspacePath = try RestoredMemoryValidation.path(row["destination_workspace_path"]); workspace = try MaintenanceFileIdentity(row["destination_workspace_identity"])
        let present = try RestoredMemoryValidation.bool(row["destination_scope_present"])
        _ = try RestoredMemoryValidation.generation(row["destination_generation"], present: present)
        coreDigest = try RestoredMemoryValidation.hash(row["core_review_digest"])
        schemaBefore = try MaintenanceValidation.integer(row["schema_before"], max: 2)
        migrationRequired = try RestoredMemoryValidation.bool(row["migration_required"])
        guard [1, 2].contains(schemaBefore), migrationRequired == (schemaBefore == 1) else { throw RPCFailure.invalidResponse }
        guard let sourceKind = row["source_kind"].string, RestoredMemoryValidation.sourceKinds.contains(sourceKind),
              let sidecars = row["cold_sqlite_recovery_required"].bool,
              sidecars == ["cold_wal_snapshot", "cold_journal_snapshot"].contains(sourceKind) else { throw RPCFailure.invalidResponse }
        self.sourceKind = sourceKind; coldSQLiteRecoveryRequired = sidecars
        let maxima: [String: Int] = ["document_bytes":1_048_576, "scope_documents":100_000, "scope_bytes":268_435_456, "memory_documents":100_000, "memory_scopes":100_000, "memory_text_bytes":268_435_456, "chunk_bytes":32_768, "copy_receipts":256, "copy_mapping_rows":100_000, "copy_metadata_bytes":16_777_216, "copy_vm_steps":20_000_000, "memory_file_bytes":67_108_864, "profile_snapshot_bytes":134_217_728]
        try MaintenanceValidation.keys(row["limits"], Set(maxima.keys))
        for (key, max) in maxima { guard try MaintenanceValidation.integer(row["limits"][key], max: max) > 0 else { throw RPCFailure.invalidResponse } }
        guard row["limits"]["chunk_bytes"].integer! >= 512, documents <= row["limits"]["scope_documents"].integer!, bytes <= row["limits"]["scope_bytes"].integer!, largest <= row["limits"]["document_bytes"].integer! else { throw RPCFailure.invalidResponse }
        expiresAt = now.addingTimeInterval(Double(try MaintenanceValidation.integer(value["expires_in_seconds"], max: 120)))
        metadata = row
    }
    public func matches(_ operation: MaintenanceOperation) -> Bool {
        operation.kind == RestoredMemoryValidation.effect && operation.scope == scope && operation.reviewID == id && operation.reviewDigest == digest && (operation.memoryCopyReceipt == nil || (operation.memoryCopyReceipt?.requestDigest == digest && operation.memoryCopyReceipt?.coreDigest == coreDigest))
    }
    public func matches(_ ticket: MaintenanceApproval, operation: MaintenanceOperation, now: Date = Date()) -> Bool {
        matches(operation) && operation.phase == "awaiting_owner" && ticket.operationID == operation.id && ticket.purpose == RestoredMemoryValidation.effect && ticket.scope == scope && ticket.reviewID == id && ticket.reviewDigest == digest && ticket.reviewMetadata == metadata && expiresAt > now && ticket.expiresAt > now
    }
    public func matches(_ inventory: RestoredMemoryInventory, source: RestoredMemorySource) -> Bool {
        scope.profileID == inventory.scope.profileID && root == inventory.root && inventoryID == inventory.id && agentID == inventory.agentID && agentRevision == inventory.agentRevision && sourceKind == inventory.sourceKind && sourceHandle == source.id && sourceGeneration == source.generation && sourceFingerprint == source.fingerprint && documents == source.documents && bytes == source.bytes && workspacePath == inventory.workspacePath && workspace == inventory.workspace && inventory.destinationEmpty && source.eligible
    }
}

public struct MemoryCopyReceipt: Sendable {
    public let operationID: String
    public let requestDigest: String
    public let coreDigest: String
    public let status: String
    public let effect: String
    public let documents: Int
    public let bytes: Int
    public let mappingHash: String?
    public let destinationGeneration: String?
    public let errorCode: String?
    public let cleanupPending: Bool
    init(_ value: RPCValue, operationID: String) throws {
        try MaintenanceValidation.keys(value, ["operation_id", "purpose", "request_digest", "core_review_digest", "status", "effect_status", "document_count", "byte_length", "mapping_sha256", "destination_generation_after", "error_code", "cleanup_pending"])
        guard value["operation_id"] == .string(operationID), value["purpose"] == .string(RestoredMemoryValidation.effect), let status = value["status"].string, ["completed", "cancelled", "failed", "unknown"].contains(status), let effect = value["effect_status"].string else { throw RPCFailure.invalidResponse }
        self.operationID = operationID; requestDigest = try RestoredMemoryValidation.hash(value["request_digest"]); coreDigest = try RestoredMemoryValidation.hash(value["core_review_digest"])
        self.status = status; self.effect = effect
        documents = try MaintenanceValidation.integer(value["document_count"], max: 100_000); bytes = try MaintenanceValidation.integer(value["byte_length"], max: 67_108_864)
        mappingHash = value["mapping_sha256"] == .null ? nil : try RestoredMemoryValidation.hash(value["mapping_sha256"])
        destinationGeneration = value["destination_generation_after"] == .null ? nil : try RestoredMemoryValidation.decimal(value["destination_generation_after"])
        errorCode = try MaintenanceValidation.code(value["error_code"]); cleanupPending = try RestoredMemoryValidation.bool(value["cleanup_pending"])
        switch status {
        case "completed": guard effect == "scope_copy_committed", !cleanupPending, errorCode == nil, documents > 0, bytes >= documents, mappingHash != nil, destinationGeneration != nil else { throw RPCFailure.invalidResponse }
        case "unknown": guard effect == "scope_copy_unknown" else { throw RPCFailure.invalidResponse }
        default: guard effect == "no_scope_copy_committed", documents == 0, bytes == 0, mappingHash == nil, destinationGeneration == nil else { throw RPCFailure.invalidResponse }
        }
    }
}

public struct MemoryCopyObservation: Sendable {
    public let operationID: String
    public let requestDigest: String
    public let documents: Int
    public let bytes: Int
    public let mappingHash: String
    public let matchingDocuments: Int
    public let destinationGeneration: String
    public let documentsMatch: Bool
    public let generationMatches: Bool
    public init(_ result: RPCValue) throws {
        try MaintenanceValidation.keys(result, ["observation", "permits_replay"])
        guard result["permits_replay"] == .bool(false) else { throw RPCFailure.invalidResponse }
        let row = result["observation"]
        try MaintenanceValidation.keys(row, ["operation_id", "request_digest", "origin_kind", "document_count", "byte_length", "mapping_sha256", "current_matching_documents", "current_destination_generation", "current_documents_match", "current_scope_generation_matches", "state", "prior_acknowledgement"])
        guard row["origin_kind"] == .string("local"), row["state"] == .string("local_record_present"), row["prior_acknowledgement"] == .string("not_established") else { throw RPCFailure.invalidResponse }
        operationID = try RestoredMemoryValidation.id(row["operation_id"]); requestDigest = try RestoredMemoryValidation.hash(row["request_digest"])
        documents = try MaintenanceValidation.integer(row["document_count"], max: 100_000); bytes = try MaintenanceValidation.integer(row["byte_length"], max: 67_108_864)
        mappingHash = try RestoredMemoryValidation.hash(row["mapping_sha256"])
        matchingDocuments = try MaintenanceValidation.integer(row["current_matching_documents"], max: documents)
        destinationGeneration = try RestoredMemoryValidation.decimal(row["current_destination_generation"])
        documentsMatch = try RestoredMemoryValidation.bool(row["current_documents_match"]); generationMatches = try RestoredMemoryValidation.bool(row["current_scope_generation_matches"])
        guard documents > 0, bytes >= documents, documentsMatch == (matchingDocuments == documents) else { throw RPCFailure.invalidResponse }
    }
    static func validateResult(_ result: RPCValue) throws {
        try MaintenanceValidation.keys(result, ["observation", "permits_replay"])
        guard result["permits_replay"] == .bool(false) else { throw RPCFailure.invalidResponse }
        if result["observation"] != .null { _ = try Self(result) }
    }
}
