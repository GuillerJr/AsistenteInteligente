import Foundation

public enum MaintenanceValidation {
    static let roles = Set(["state", "memory", "schedules", "agents", "skills", "telegram", "configuration_portable"])
    static let exclusions = Set(["provider_credentials_and_state", "transport_tokens_sockets_and_locks", "mcp_programs_arguments_and_runtime_homes", "external_and_internal_workspace_files", "external_skill_resources", "temporary_sessions_and_memory", "live_approvals_and_grants", "running_processes_audio_and_network_clients", "host_maintenance_journal"])
    static let disarms = Set(["provider_threads_and_bindings_cleared", "workspace_unselected", "mcp_programs_absent", "schedules_disabled", "telegram_routes_disabled", "skills_disabled", "unfinished_records_interrupted_unknown_no_replay", "fresh_transport_required", "fresh_channel_pairing_and_delivery_cutoff_required", "memory_workspace_association_requires_separate_review"])
    public static func canonical(_ value: RPCValue) throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(value)
        guard data.count <= 16_384, let text = String(data: data, encoding: .utf8) else { throw RPCFailure.invalidResponse }
        return text
    }
    static func keys(_ value: RPCValue, _ expected: Set<String>) throws {
        guard let fields = value.object, Set(fields.keys) == expected else { throw RPCFailure.invalidResponse }
    }
    static func safeID(_ value: String) -> Bool { value.range(of: "^[A-Za-z0-9_-]{1,128}\\z", options: .regularExpression) != nil }
    static func integer(_ value: RPCValue, max: Int = 4_503_599_627_370_495) throws -> Int {
        guard let result = value.integer, result >= 0, result <= max else { throw RPCFailure.invalidResponse }; return result
    }
    static func code(_ value: RPCValue) throws -> String? {
        if value == .null { return nil }
        guard let text = value.string, text.range(of: "^[a-z][a-z0-9_]{0,127}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }; return text
    }
    static func stage(_ value: RPCValue) throws -> String? {
        if value == .null { return nil }
        guard let text = value.string, text.range(of: "^[a-z][a-z0-9_.]{0,127}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }; return text
    }
    public static func path(_ path: String) -> Bool {
        path.hasPrefix("/") && path.utf8.count <= 4096 && !path.contains("\0") && !path.split(separator: "/").contains("..")
    }
    public static func name(_ name: String) -> Bool { name.range(of: "^[A-Za-z0-9][A-Za-z0-9_-]{0,95}\\z", options: .regularExpression) != nil }
    static func strings(_ value: RPCValue, exact: Set<String>) throws -> [String] {
        guard let rows = value.array, rows.count == exact.count else { throw RPCFailure.invalidResponse }
        let texts = try rows.map { row -> String in guard let text = row.string else { throw RPCFailure.invalidResponse }; return text }
        guard Set(texts) == exact else { throw RPCFailure.invalidResponse }; return texts
    }
}

public struct MaintenanceScope: Equatable, Sendable {
    public let profileID: String
    public let generation: Int
    public init(_ value: RPCValue) throws {
        guard let profile = value["profile_id"].string, MaintenanceValidation.safeID(profile) else { throw RPCFailure.invalidResponse }
        profileID = profile; generation = try MaintenanceValidation.integer(value["generation"])
    }
}

public struct MaintenanceFileIdentity: Hashable, Sendable {
    public let device: String
    public let inode: String
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["device", "inode"])
        let parts = try ["device", "inode"].map { key -> String in
            guard let text = value[key].string, text.range(of: "^[0-9]{1,20}\\z", options: .regularExpression) != nil, UInt64(text) != nil else { throw RPCFailure.invalidResponse }; return text
        }
        device = parts[0]; inode = parts[1]
    }
}

public struct MaintenanceSource: Identifiable, Sendable {
    public let role: String
    public let disposition: String
    public let bytes: Int
    public var id: String { role }
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["role", "disposition", "bytes"])
        guard let role = value["role"].string, MaintenanceValidation.roles.contains(role),
              let state = value["disposition"].string, ["owned", "absent", "unavailable", "present", "generated_builtin"].contains(state) else { throw RPCFailure.invalidResponse }
        self.role = role; disposition = state; bytes = try MaintenanceValidation.integer(value["bytes"], max: 134_217_728)
    }
}

public struct MaintenanceSqliteRecovery: Identifiable, Sendable {
    public let role: String
    public let mode: String
    public let semantics: String
    public var id: String { "maintenance-recovery:\(role)" }
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["role", "mode", "semantics"])
        guard let role = value["role"].string, ["state", "memory", "schedules"].contains(role),
              let mode = value["mode"].string,
              let semantics = value["semantics"].string else { throw RPCFailure.invalidResponse }
        let expected: String
        switch mode {
        case "cold_wal": expected = "confirmed_transactions_only"
        case "cold_wal_main_only", "cold_wal_empty", "cold_wal_header": expected = "durable_main_state_missing_transactions_unknown"
        case "cold_journal_hot", "cold_journal_inactive", "cold_journal": expected = "journal_recovery_private_copy"
        default: throw RPCFailure.invalidResponse
        }
        guard semantics == expected else { throw RPCFailure.invalidResponse }
        self.role = role; self.mode = mode; self.semantics = semantics
    }
}

public struct MaintenanceReview: Identifiable, Sendable {
    public let id: String
    public let digest: String
    public let kind: String
    public let scope: MaintenanceScope
    public let destinationParent: String
    public let destinationName: String
    public let destinationIdentity: MaintenanceFileIdentity
    public let sources: [MaintenanceSource]
    public let sqliteRecovery: [MaintenanceSqliteRecovery]
    public let expiresAt: Date
    public let metadata: RPCValue
    public var params: [String: RPCValue] { ["review_id": .string(id), "review_digest": .string(digest)] }
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "expires_in_seconds", "metadata"])
        guard let id = value["review_id"].string, ManagementValidation.identifier(id),
              let digest = value["review_digest"].string, ManagementValidation.hash(digest),
              let kind = value["metadata"]["kind"].string, ["backup.create", "restore.stage"].contains(kind),
              let parent = value["metadata"]["destination_parent"].string, MaintenanceValidation.path(parent),
              let name = value["metadata"]["destination_name"].string, MaintenanceValidation.name(name)
        else { throw RPCFailure.invalidResponse }
        let metadata = value["metadata"]
        var keys: Set<String> = ["kind", "profile_id", "generation", "source", "destination_parent", "destination_name", "destination_identity", "excluded", "disarms", "workspace_files_included", "credentials_included", "activation_included", "authenticity", "content_warning"]
        if kind == "restore.stage" { keys.formUnion(["manifest_sha256", "artifact_id", "source_artifact_identity"]) }
        if kind == "backup.create", metadata.object?["sqlite_recovery"] != nil { keys.insert("sqlite_recovery") }
        try MaintenanceValidation.keys(metadata, keys)
        guard metadata["workspace_files_included"] == .bool(false), metadata["credentials_included"] == .bool(false),
              metadata["activation_included"] == .bool(false), metadata["authenticity"] == .string("not_established"),
              metadata["content_warning"] == .string("user_content_may_contain_secrets"),
              ManagementValidation.digest(try MaintenanceValidation.canonical(metadata)) == digest else { throw RPCFailure.invalidResponse }
        _ = try MaintenanceValidation.strings(metadata["excluded"], exact: MaintenanceValidation.exclusions)
        _ = try MaintenanceValidation.strings(metadata["disarms"], exact: kind == "restore.stage" ? MaintenanceValidation.disarms : [])
        if kind == "restore.stage" {
            guard let hash = metadata["manifest_sha256"].string, ManagementValidation.hash(hash),
                  let artifact = metadata["artifact_id"].string, ManagementValidation.identifier(artifact) else { throw RPCFailure.invalidResponse }
            _ = try MaintenanceFileIdentity(metadata["source_artifact_identity"])
        }
        let sources = try ManagementValidation.rows(metadata["source"], limit: 7, parse: MaintenanceSource.init)
        guard Set(sources.map(\.role)) == MaintenanceValidation.roles.subtracting(["configuration_portable"]), sources.count == 6 else { throw RPCFailure.invalidResponse }
        let recovery = try metadata.object?["sqlite_recovery"].map { try ManagementValidation.rows($0, limit: 3, parse: MaintenanceSqliteRecovery.init) } ?? []
        guard Set(recovery.map(\.role)).count == recovery.count,
              recovery.allSatisfy({ row in sources.contains { $0.role == row.role && $0.disposition == "present" } }) else { throw RPCFailure.invalidResponse }
        sqliteRecovery = recovery
        let lifetime = try MaintenanceValidation.integer(value["expires_in_seconds"], max: 120)
        guard lifetime > 0 else { throw RPCFailure.invalidResponse }
        self.id = id; self.digest = digest; self.kind = kind; self.metadata = metadata
        scope = try MaintenanceScope(metadata); destinationParent = parent; destinationName = name
        destinationIdentity = try MaintenanceFileIdentity(metadata["destination_identity"]); self.sources = sources
        expiresAt = now.addingTimeInterval(Double(lifetime))
    }
}

public struct MaintenanceApproval: Identifiable, Sendable {
    public let id: String
    public let operationID: String
    public let purpose: String
    public let digest: String
    public let scope: MaintenanceScope
    public let reviewID: String
    public let reviewDigest: String
    public let reviewMetadata: RPCValue
    public let expiresAt: Date
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["approval_id", "scope", "purpose", "operation_id", "arguments_json", "digest", "expires_at_ns"])
        guard let id = value["approval_id"].string, ManagementValidation.identifier(id),
              let operation = value["operation_id"].string, ManagementValidation.identifier(operation),
              let purpose = value["purpose"].string, Set(["backup.create", "restore.stage", "profile.restore.activate", "memory.restore.copy_scope", ChannelReconciliationValidation.effect]).union(RecoveryExtensionValidation.effects).contains(purpose),
              let digest = value["digest"].string, ManagementValidation.hash(digest),
              let arguments = value["arguments_json"].string, arguments.utf8.count <= 16_384,
              let expiry = value["expires_at_ns"].string, expiry.range(of: "^[0-9]{1,20}\\z", options: .regularExpression) != nil,
              let ns = UInt64(expiry) else { throw RPCFailure.invalidResponse }
        let args = try JSONDecoder().decode(RPCValue.self, from: Data(arguments.utf8))
        try MaintenanceValidation.keys(args, ["review_id", "review_digest", "review"])
        guard let reviewID = args["review_id"].string, ManagementValidation.identifier(reviewID),
              let reviewDigest = args["review_digest"].string, ManagementValidation.hash(reviewDigest),
              try MaintenanceValidation.canonical(args) == arguments,
              ManagementValidation.digest(try MaintenanceValidation.canonical(args["review"])) == reviewDigest else { throw RPCFailure.invalidResponse }
        let scope = try MaintenanceScope(value["scope"])
        try MaintenanceValidation.keys(value["scope"], ["profile_id", "generation"])
        let identity: RPCValue = .object(["domain": .string("jarvis.host.approval/1"), "profile": .string(scope.profileID), "generation": .number(Double(scope.generation)), "purpose": .string(purpose), "operation": .string(operation), "arguments_json": .string(arguments)])
        guard ManagementValidation.digest(try MaintenanceValidation.canonical(identity)) == digest else { throw RPCFailure.invalidResponse }
        self.id = id; operationID = operation; self.purpose = purpose; self.digest = digest; self.scope = scope
        self.reviewID = reviewID; self.reviewDigest = reviewDigest; reviewMetadata = args["review"]
        expiresAt = Date(timeIntervalSince1970: Double(ns) / 1_000_000_000)
    }
    public func matches(_ review: MaintenanceReview, operation: MaintenanceOperation, now: Date = Date()) -> Bool {
        operation.matches(review) && operationID == operation.id && purpose == operation.kind && purpose == review.kind && scope == review.scope && reviewID == review.id && reviewDigest == review.digest && reviewMetadata == review.metadata && expiresAt > now
    }
    public var params: [String: RPCValue] { ["approval_id": .string(id), "digest": .string(digest), "profile_id": .string(scope.profileID), "generation": .number(Double(scope.generation))] }
}

public struct MaintenanceReceipt: Sendable {
    public let status: String
    public let effect: String
    public let targetName: String
    public let artifactID: String?
    public let manifestHash: String?
    public let cleanupPending: Bool
    init(_ value: RPCValue, operation: String, kind: String) throws {
        try MaintenanceValidation.keys(value, ["operation_id", "purpose", "status", "effect_status", "target_name", "artifact_id", "manifest_sha256", "target_identity", "new_profile_id", "error_code", "cleanup_error_code", "cleanup_pending"])
        guard value["operation_id"] == .string(operation), value["purpose"] == .string(kind),
              let status = value["status"].string, ["completed", "cancelled", "failed", "unknown"].contains(status),
              let effect = value["effect_status"].string, ["published_verified_bytes", "new_disarmed_profile", "no_published_effect", "unknown", "current_selected", "publication_unknown", "no_current_change"].contains(effect),
              let name = value["target_name"].string, MaintenanceValidation.name(name),
              let cleanup = value["cleanup_pending"].bool else { throw RPCFailure.invalidResponse }
        for key in ["artifact_id", "new_profile_id"] { if value[key] != .null { guard let id = value[key].string, ManagementValidation.identifier(id) else { throw RPCFailure.invalidResponse } } }
        if value["manifest_sha256"] != .null { guard let hash = value["manifest_sha256"].string, ManagementValidation.hash(hash) else { throw RPCFailure.invalidResponse } }
        if value["target_identity"] != .null { _ = try MaintenanceFileIdentity(value["target_identity"]) }
        _ = try MaintenanceValidation.code(value["error_code"]); _ = try MaintenanceValidation.code(value["cleanup_error_code"])
        if status == "completed" {
            let release = ["release.update.apply", "release.rollback.apply"].contains(kind)
            let expectedEffect = release ? "current_selected" : kind == "restore.stage" ? "new_disarmed_profile" : "published_verified_bytes"
            guard !cleanup, value["target_identity"] != .null,
                  release || value["artifact_id"] != .null,
                  value["manifest_sha256"] != .null, effect == expectedEffect else { throw RPCFailure.invalidResponse }
            if kind == "restore.stage", value["new_profile_id"] == .null { throw RPCFailure.invalidResponse }
        }
        self.status = status; self.effect = effect; targetName = name; artifactID = value["artifact_id"].string; manifestHash = value["manifest_sha256"].string; cleanupPending = cleanup
    }
}

public struct MaintenanceOperation: Identifiable, Sendable {
    public let id: String
    public let scope: MaintenanceScope
    public let reviewID: String?
    public let reviewDigest: String?
    public let kind: String
    public let phase: String
    public let stage: String?
    public let workerPending: Bool
    public let cancellationRequested: Bool
    public let receiptDurable: Bool
    public let receipt: MaintenanceReceipt?
    public let activationReceipt: RestoreActivationReceipt?
    public let memoryCopyReceipt: MemoryCopyReceipt?
    public let channelReceipt: ChannelReconciliationReceipt?
    public let cleanupOwnerPending: Bool
    public let errorCode: String?
    public let result: RPCValue
    public static let terminal = Set(["complete", "staged_disarmed", "activated_not_opened", "cancelled", "denied", "failed", "publication_unknown", "cleanup_pending"])
    public func matches(_ review: MaintenanceReview) -> Bool {
        scope == review.scope && kind == review.kind && reviewID == review.id && reviewDigest == review.digest
    }
    public func follows(_ previous: MaintenanceOperation) -> Bool {
        guard id == previous.id, kind == previous.kind, scope.profileID == previous.scope.profileID,
              reviewID == previous.reviewID, reviewDigest == previous.reviewDigest else { return false }
        if ["backup.verify", "doctor.report", "profile.restore.review", "profile.restore.inspect"].contains(kind) || RestoredMemoryValidation.reads.contains(kind) || RecoveryExtensionValidation.reads.contains(kind) { return scope.generation >= previous.scope.generation }
        return scope == previous.scope
    }
    public var settled: Bool { Self.terminal.contains(phase) && !workerPending }
    /// Worker completion and native resource retirement are separate facts.
    /// Historical receipt cleanup flags do not represent current RAM custody.
    public var resourcesPending: Bool { !settled || cleanupOwnerPending || phase == "cleanup_pending" }
    public var success: Bool {
        guard !cleanupOwnerPending else { return false }
        if kind == ChannelReconciliationValidation.effect { return settled && phase == "complete" && receiptDurable && channelReceipt?.status == "completed" }
        if kind == RestoredMemoryValidation.effect { return settled && phase == "complete" && receiptDurable && !cleanupOwnerPending && memoryCopyReceipt?.status == "completed" }
        if RestoredMemoryValidation.reads.contains(kind) || RecoveryExtensionValidation.reads.contains(kind) { return settled && phase == "complete" }
        if kind == "profile.restore.activate" { return settled && phase == "activated_not_opened" && receiptDurable && !cleanupOwnerPending && activationReceipt?.status == "completed" }
        return settled && ["complete", "staged_disarmed"].contains(phase) && (["doctor.report", "backup.verify", "profile.restore.review", "profile.restore.inspect"].contains(kind) || (receiptDurable && receipt?.status == "completed"))
    }
    public init(_ value: RPCValue) throws {
        var keys: Set<String> = ["operation_id", "kind", "phase", "stage", "result", "error_code", "receipt", "receipt_durable", "worker_pending", "cancellation_requested", "scope", "review_id", "review_digest"]
        if value.object?["cleanup_owner_pending"] != nil { keys.insert("cleanup_owner_pending") }
        try MaintenanceValidation.keys(value, keys)
        if keys.contains("cleanup_owner_pending") { guard let pending = value["cleanup_owner_pending"].bool else { throw RPCFailure.invalidResponse }; cleanupOwnerPending = pending }
        else { cleanupOwnerPending = false }
        guard let id = value["operation_id"].string, ManagementValidation.identifier(id),
              let kind = value["kind"].string, Set(["backup.create", "restore.stage", "backup.verify", "doctor.report", "profile.restore.review", "profile.restore.inspect", "profile.restore.activate"]).union(RestoredMemoryValidation.kinds).union([ChannelReconciliationValidation.effect]).union(RecoveryExtensionValidation.kinds).contains(kind),
              let phase = value["phase"].string, Self.terminal.union(["awaiting_owner", "quiescing", "working"]).contains(phase),
              let pending = value["worker_pending"].bool, let cancelled = value["cancellation_requested"].bool,
              let durable = value["receipt_durable"].bool else { throw RPCFailure.invalidResponse }
        if (kind.hasPrefix("profile.restore.") || RestoredMemoryValidation.kinds.contains(kind) || kind == ChannelReconciliationValidation.effect), !keys.contains("cleanup_owner_pending") { throw RPCFailure.invalidResponse }
        self.id = id; self.kind = kind; self.phase = phase; workerPending = pending; cancellationRequested = cancelled; receiptDurable = durable
        try MaintenanceValidation.keys(value["scope"], ["profile_id", "generation"])
        scope = try MaintenanceScope(value["scope"])
        if Set(["backup.create", "restore.stage", "profile.restore.activate", "memory.restore.copy_scope", ChannelReconciliationValidation.effect]).union(RecoveryExtensionValidation.effects).contains(kind) {
            guard let review = value["review_id"].string, ManagementValidation.identifier(review),
                  let digest = value["review_digest"].string, ManagementValidation.hash(digest) else { throw RPCFailure.invalidResponse }
            reviewID = review; reviewDigest = digest
        } else {
            guard value["review_id"] == .null, value["review_digest"] == .null else { throw RPCFailure.invalidResponse }
            reviewID = nil; reviewDigest = nil
        }
        stage = try MaintenanceValidation.stage(value["stage"]); errorCode = try MaintenanceValidation.code(value["error_code"])
        if kind == ChannelReconciliationValidation.effect {
            receipt = nil; activationReceipt = nil; memoryCopyReceipt = nil
            channelReceipt = value["receipt"] == .null ? nil : try ChannelReconciliationReceipt(value["receipt"], operationID: id)
            if let channelReceipt, channelReceipt.requestDigest != reviewDigest { throw RPCFailure.invalidResponse }
        } else if kind == "profile.restore.activate" {
            channelReceipt = nil; memoryCopyReceipt = nil; receipt = nil; activationReceipt = value["receipt"] == .null ? nil : try RestoreActivationReceipt(value["receipt"], operationID: id)
        } else if kind == RestoredMemoryValidation.effect {
            channelReceipt = nil; receipt = nil; activationReceipt = nil; memoryCopyReceipt = value["receipt"] == .null ? nil : try MemoryCopyReceipt(value["receipt"], operationID: id)
            if let memoryCopyReceipt, memoryCopyReceipt.requestDigest != reviewDigest { throw RPCFailure.invalidResponse }
        } else {
            channelReceipt = nil; memoryCopyReceipt = nil; activationReceipt = nil; receipt = value["receipt"] == .null ? nil : try MaintenanceReceipt(value["receipt"], operation: id, kind: kind)
        }
        result = value["result"]
        guard result == .null || result.object != nil else { throw RPCFailure.invalidResponse }
        if kind == ChannelReconciliationValidation.effect {
            guard result == .null else { throw RPCFailure.invalidResponse }
            if phase == "complete" { guard durable, channelReceipt?.status == "completed", !cleanupOwnerPending else { throw RPCFailure.invalidResponse } }
        }
        if RestoredMemoryValidation.kinds.contains(kind) {
            if kind == RestoredMemoryValidation.effect {
                guard result == .null else { throw RPCFailure.invalidResponse }
                if phase == "complete" { guard durable, memoryCopyReceipt?.status == "completed", !cleanupOwnerPending else { throw RPCFailure.invalidResponse } }
            } else {
                guard value["receipt"] == .null, !durable else { throw RPCFailure.invalidResponse }
                if result != .null {
                    if kind == "memory.restore.inventory" { try MaintenanceValidation.keys(result, ["inventory"]); _ = try RestoredMemoryInventory(result["inventory"]) }
                    else if kind == "memory.restore.review" { try MaintenanceValidation.keys(result, ["review"]); _ = try RestoredMemoryReview(result["review"]) }
                    else { try MemoryCopyObservation.validateResult(result) }
                } else if phase == "complete" { throw RPCFailure.invalidResponse }
            }
        }
        if RecoveryExtensionValidation.kinds.contains(kind) {
            try RecoveryExtensionValidation.validateResult(kind: kind, result: result, phase: phase, receiptDurable: durable, hasReceipt: value["receipt"] != .null)
            if let payload = result.object?["review"] {
                let prepared = try RecoveryExtensionReview(payload)
                guard prepared.scope == scope else { throw RPCFailure.invalidResponse }
            }
        }
        if phase == "complete", kind == "backup.verify" { _ = try MaintenanceVerification(result) }
        if phase == "complete", kind == "doctor.report" { _ = try DoctorSummary(result) }
        if ["complete", "staged_disarmed", "activated_not_opened"].contains(phase), cleanupOwnerPending { throw RPCFailure.invalidResponse }
        if phase == "staged_disarmed", kind != "restore.stage" { throw RPCFailure.invalidResponse }
        if phase == "activated_not_opened" { guard kind == "profile.restore.activate", durable, activationReceipt?.status == "completed", !cleanupOwnerPending else { throw RPCFailure.invalidResponse } }
        if ["complete", "staged_disarmed"].contains(phase), Set(["backup.create", "restore.stage"]).union(RecoveryExtensionValidation.effects).contains(kind) {
            guard durable, receipt?.status == "completed", !cleanupOwnerPending else { throw RPCFailure.invalidResponse }
        }
    }
}

public struct MaintenanceStatus: Sendable {
    public let scope: MaintenanceScope
    public let closing: Bool
    public let operations: [MaintenanceOperation]
    public let previousCount: Int
    public let blockers: [String]
    public init(_ value: RPCValue) throws {
        var keys: Set<String> = ["operations", "previous_operations", "profile_id", "generation", "closing"]
        if value.object?["blockers"] != nil { keys.insert("blockers") }
        try MaintenanceValidation.keys(value, keys)
        if let rows = value.object?["blockers"] {
            guard let values = rows.array, values.count <= 16 else { throw RPCFailure.invalidResponse }
            blockers = try values.map { guard let code = try MaintenanceValidation.code($0) else { throw RPCFailure.invalidResponse }; return code }
        } else { blockers = [] }
        scope = try MaintenanceScope(value)
        guard let closing = value["closing"].bool, let previous = value["previous_operations"].array, previous.count <= 64 else { throw RPCFailure.invalidResponse }
        self.closing = closing; previousCount = previous.count
        operations = try ManagementValidation.rows(value["operations"], limit: 16, parse: MaintenanceOperation.init)
        guard Set(operations.map(\.id)).count == operations.count,
              operations.allSatisfy({ $0.scope.profileID == scope.profileID && $0.scope.generation <= scope.generation }) else { throw RPCFailure.invalidResponse }
    }
}

public struct MaintenancePending: Sendable {
    public let scope: MaintenanceScope
    public let approvals: [MaintenanceApproval]
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["profile_id", "generation", "pending"])
        scope = try MaintenanceScope(value)
        approvals = try ManagementValidation.rows(value["pending"], limit: 16, parse: MaintenanceApproval.init)
        guard Set(approvals.map(\.id)).count == approvals.count, approvals.allSatisfy({ $0.scope == scope }) else { throw RPCFailure.invalidResponse }
    }
}

public struct MaintenanceVerification: Sendable {
    public let id: String
    public let manifestHash: String
    public let artifactID: String
    public init(_ result: RPCValue) throws {
        try MaintenanceValidation.keys(result, ["verification_id", "verification"])
        guard let id = result["verification_id"].string, ManagementValidation.identifier(id),
              let hash = result["verification"]["manifest_sha256"].string, ManagementValidation.hash(hash),
              let artifact = result["verification"]["artifact_id"].string, ManagementValidation.identifier(artifact),
              result["verification"]["authenticity"] == .string("not_established") else { throw RPCFailure.invalidResponse }
        try MaintenanceValidation.keys(result["verification"], ["artifact_id", "profile_id", "generation", "manifest_sha256", "root_identity", "resource_hashes", "excluded", "authenticity"])
        _ = try MaintenanceScope(result["verification"])
        _ = try MaintenanceValidation.strings(result["verification"]["excluded"], exact: MaintenanceValidation.exclusions)
        guard let hashes = result["verification"]["resource_hashes"].array, !hashes.isEmpty, hashes.count <= 7 else { throw RPCFailure.invalidResponse }
        var seen = Set<String>()
        for hash in hashes {
            guard let pair = hash.array, pair.count == 2, let role = pair[0].string, MaintenanceValidation.roles.contains(role), seen.insert(role).inserted, let digest = pair[1].string, ManagementValidation.hash(digest) else { throw RPCFailure.invalidResponse }
        }
        self.id = id; manifestHash = hash; artifactID = artifact
        _ = try MaintenanceFileIdentity(result["verification"]["root_identity"])
    }
}

public enum MaintenancePresentation {
    public static func operationError(_ code: String) -> String? {
        if code.hasPrefix("profile_cold_journal_") {
            return "No se pudo validar el journal SQLite; el diagnóstico no acredita recuperación. Consulta el resultado, no se repetirá automáticamente."
        }
        switch code {
        case "maintenance_review_expired", "memory_restore_review_expired", "activation_review_expired", "restore_review_expired", "telegram_reconciliation_expired":
            return "La revisión caducó. Ya no puede autorizarse; consulta el estado y prepara otra cuando termine la espera anterior. No se repetirá la operación automáticamente."
        case "telegram_restore_profile_required":
            return "Abre explícitamente un perfil restaurado y admitido para reconciliar sus rutas."
        case "telegram_reconciliation_changed", "telegram_reconciliation_no_change":
            return "La ruta, el agente o la carpeta cambiaron desde la revisión. Consulta el estado y prepara otra revisión exacta."
        case "telegram_restore_bot_required":
            return "El perfil no conserva la identidad de un bot de Telegram. Añade un enlace nuevo desde el panel Telegram."
        case "profile_cold_wal_cleanup_pending":
            return "La limpieza de la copia privada WAL sigue sin confirmar. Completa sólo esa limpieza antes de continuar."
        case "profile_cold_wal_invalid", "profile_cold_wal_no_commit":
            return "No se pudo acreditar una familia WAL válida con transacciones confirmadas. No se publicará una copia incompleta."
        case "profile_cold_wal_changed", "profile_sqlite_family_changed":
            return "La familia SQLite cambió durante la revisión o la lectura. Prepara una revisión nueva cuando termine su actividad."
        case "profile_cold_wal_size_limit", "profile_cold_wal_work_limit", "profile_sqlite_image_limit":
            return "La recuperación WAL supera los límites de tamaño o trabajo admitidos. No se truncarán los datos para crear la copia."
        case "profile_cold_wal_unavailable", "profile_cold_wal_sqlite_unavailable":
            return "Esta familia SQLite no admite la recuperación WAL disponible. El original no se abrirá para intentar repararlo."
        case "recovery_cancelled", "recovery_deadline_exceeded":
            return "La operación se detuvo. Su limpieza debe quedar confirmada antes de iniciar otra."
        default: return nil
        }
    }
    public static func error(_ error: Error) -> String {
        if let failure = error as? RPCFailure, case .remote(_, let code) = failure {
            if let message = operationError(code) { return message }
            if code == "profile_lazy_source_unavailable" { return "Hay un almacén aún no abierto; no se modificó para preparar el respaldo. Consulta ese almacén desde Jarvis y vuelve a revisar." }
            return "La revisión no está vigente o el mantenimiento está bloqueado. Actualiza el estado y revisa de nuevo; no se repetirá ninguna operación."
        }
        return (error as? RPCFailure)?.localizedDescription ?? "No se pudo confirmar el mantenimiento. Actualiza el estado; la operación no se repetirá automáticamente."
    }
    public static func phase(_ phase: String) -> String {
        ["awaiting_owner":"Esperando tu aprobación", "quiescing":"Comprobando disponibilidad", "working":"En curso", "complete":"Completada", "staged_disarmed":"Perfil preparado y desarmado", "activated_not_opened":"Perfil activado; aún sin abrir", "cancelled":"Cancelada", "denied":"No autorizada", "failed":"Fallida", "publication_unknown":"Resultado sin confirmar", "cleanup_pending":"Limpieza pendiente"][phase] ?? "Sin confirmar"
    }
    public static func role(_ role: String) -> String {
        ["state":"Conversaciones y recibos", "memory":"Memoria", "schedules":"Tareas", "agents":"Agentes", "skills":"Skills", "telegram":"Registro de Telegram", "configuration_portable":"Configuración del respaldo", "cross_references":"Relaciones entre recursos"][role] ?? "Recurso propio"
    }
}

public struct DoctorCheckSummary: Identifiable, Sendable {
    public let role: String
    public let status: String
    public let integrity: String
    public let source: String
    public var id: String { role }
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["role", "status", "code", "identity", "permissions", "byte_length", "format_version", "application_id", "schema_version", "integrity", "counts", "inspection_source", "snapshot_byte_length", "snapshot_sha256"])
        guard let role = value["role"].string, MaintenanceValidation.roles.union(["cross_references", "restore_record"]).contains(role),
              let status = value["status"].string, ["ok", "absent", "uninitialized", "unsafe", "corrupt", "blocked", "unknown"].contains(status),
              let integrity = value["integrity"].string, ["ok", "not_checked"].contains(integrity),
              let source = value["inspection_source"].string, ["metadata", "standalone_file", "selected_json", "host_ram_snapshot"].contains(source),
              let counts = value["counts"].array, counts.count <= 32 else { throw RPCFailure.invalidResponse }
        for count in counts {
            guard let pair = count.array, pair.count == 2, let name = pair[0].string, name.range(of: "^[a-z_]{1,64}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }
            _ = try MaintenanceValidation.integer(pair[1])
        }
        for field in ["byte_length", "format_version", "application_id", "schema_version", "snapshot_byte_length"] { if value[field] != .null { _ = try MaintenanceValidation.integer(value[field]) } }
        _ = try MaintenanceValidation.code(value["code"])
        if value["snapshot_sha256"] != .null { guard let hash = value["snapshot_sha256"].string, ManagementValidation.hash(hash) else { throw RPCFailure.invalidResponse } }
        self.role = role; self.status = status; self.integrity = integrity; self.source = source
    }
}

public struct DoctorSummary: Sendable {
    public let status: String
    public let scope: MaintenanceScope
    public let checks: [DoctorCheckSummary]
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["report"])
        let row = value["report"]
        try MaintenanceValidation.keys(row, ["report_id", "profile_id", "generation", "status", "coherence", "profile_identity", "checks", "blockers", "suggestions", "supported_repair_actions", "effect"])
        guard let id = row["report_id"].string, ManagementValidation.identifier(id),
              let status = row["status"].string, ["ok", "blocked", "unsafe", "corrupt", "partial", "uninitialized", "absent", "unknown", "cancelled"].contains(status),
              row["effect"] == .string("read_only_diagnostic"), row["supported_repair_actions"] == .array([]),
              let coherence = row["coherence"].string, ["coherent_snapshot", "offline_quiescent", "unverified"].contains(coherence) else { throw RPCFailure.invalidResponse }
        for key in ["blockers", "suggestions"] {
            guard let values = row[key].array, values.count <= 32 else { throw RPCFailure.invalidResponse }
            for value in values { guard try MaintenanceValidation.code(value) != nil else { throw RPCFailure.invalidResponse } }
        }
        self.status = status; scope = try MaintenanceScope(row)
        checks = try ManagementValidation.rows(row["checks"], limit: 10, parse: DoctorCheckSummary.init)
        guard Set(checks.map(\.role)).count == checks.count else { throw RPCFailure.invalidResponse }
    }
}


public enum MaintenanceSubmissionReconciliation {
    /// Only the immutable operation metadata can reconcile a lost ACK. A live
    /// generation advance, expired review or absent approval ticket is allowed.
    /// No effect is replayed and no approval is inferred from this lookup.
    public static func find(_ review: MaintenanceReview, in operations: [MaintenanceOperation]) throws -> MaintenanceOperation? {
        guard operations.count <= 16 else { throw RPCFailure.invalidResponse }
        let matches = operations.filter { $0.matches(review) }
        guard matches.count <= 1 else { throw RPCFailure.invalidResponse }
        return matches.first
    }
    public static let acknowledgement = "Se cerró el aviso local. El resultado anterior sigue sin confirmar; no se reintentó la solicitud ni se declaró ausente ningún efecto. Consulta los recibos anteriores."
}
