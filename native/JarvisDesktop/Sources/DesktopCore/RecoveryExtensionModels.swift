import Foundation

public enum RecoveryExtensionValidation {
    public static let reads = Set(["workspace.backup.review", "workspace.restore.review", "doctor.repair.review", "release.review", "release.rollback.review", "release.inspect", "release.resolve_launch"])
    public static let effects = Set(["workspace.backup.create", "workspace.restore.stage", "doctor.repair.apply", "release.update.apply", "release.rollback.apply"])
    public static let kinds = reads.union(effects)
    static func text(_ value: RPCValue, max: Int = 4096) throws -> String {
        guard let result = value.string, result.utf8.count <= max, !result.contains("\0"), !result.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { throw RPCFailure.invalidResponse }; return result
    }
    static func path(_ value: RPCValue) throws -> String {
        let path = try text(value); guard MaintenanceValidation.path(path) else { throw RPCFailure.invalidResponse }; return path
    }
    static func hash(_ value: RPCValue) throws -> String {
        let text = try text(value, max: 64); guard ManagementValidation.hash(text) else { throw RPCFailure.invalidResponse }; return text
    }
    public static func relativePath(_ path: String) -> Bool {
        guard !path.isEmpty, path.utf8.count <= 1024, !path.hasPrefix("/"), !path.contains("\\"), path.utf8.elementsEqual(path.precomposedStringWithCanonicalMapping.utf8), path.utf8.elementsEqual(path.precomposedStringWithCompatibilityMapping.utf8), !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) || CharacterSet(charactersIn: ":<>\"|?*\u{202A}\u{202B}\u{202D}\u{202E}\u{2066}\u{2067}\u{2068}\u{2069}").contains($0) }) else { return false }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        return parts.count <= 16 && parts.allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." && $0.utf8.count <= 255 && !$0.hasSuffix(".") && !$0.hasSuffix(" ") }
    }
}

public struct RecoveryWorkspaceEntry: Identifiable, Sendable, Equatable {
    public let path: String
    public let role: String
    public let kind: String
    public var id: String { "selection:\(path)" }
    public var reviewedID: String { "reviewed-selection:\(path)" }
    public init(path: String, role: String = "workspace_file", kind: String = "file") throws {
        guard RecoveryExtensionValidation.relativePath(path), ["workspace_file", "artifact"].contains(role), ["file", "directory"].contains(kind) else { throw RPCFailure.configuration }
        self.path = path; self.role = role; self.kind = kind
    }
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["path", "role", "kind"])
        guard let path = value["path"].string, let role = value["role"].string, let kind = value["kind"].string else { throw RPCFailure.invalidResponse }
        do { try self.init(path: path, role: role, kind: kind) } catch { throw RPCFailure.invalidResponse }
    }
    public var wire: RPCValue { .object(["path": .string(path), "role": .string(role), "kind": .string(kind)]) }
}

public struct RecoveryWorkspaceSummary: Sendable {
    public let manifestSHA: String
    public let files: Int
    public let directories: Int
    public let artifacts: Int
    public let bytes: Int
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["manifest_sha256", "file_count", "directory_count", "artifact_count", "total_bytes"])
        manifestSHA = try RecoveryExtensionValidation.hash(value["manifest_sha256"])
        files = try MaintenanceValidation.integer(value["file_count"], max: 256); directories = try MaintenanceValidation.integer(value["directory_count"], max: 128)
        artifacts = try MaintenanceValidation.integer(value["artifact_count"], max: files); bytes = try MaintenanceValidation.integer(value["total_bytes"], max: 33_554_432)
        guard files > 0 || bytes == 0 else { throw RPCFailure.invalidResponse }
    }
}

public struct RecoveryWorkspaceDetails: Sendable {
    public let sourcePath: String
    public let sourceIdentity: MaintenanceFileIdentity
    public let destinationParent: String
    public let destinationName: String
    public let destinationIdentity: MaintenanceFileIdentity
    public let summary: RecoveryWorkspaceSummary
    public let selection: [RecoveryWorkspaceEntry]
    public let isBackup: Bool
    init(_ value: RPCValue, kind: String) throws {
        try MaintenanceValidation.keys(value, ["kind", "profile_id", "generation", "source_path", "source_identity", "destination_parent", "destination_name", "destination_identity", "summary", "selection", "semantics", "credentials_included", "activation_included", "authenticity", "content_warning"])
        isBackup = kind == "workspace.backup.create"
        guard ["workspace.backup.create", "workspace.restore.stage"].contains(kind), value["credentials_included"] == .bool(false), value["activation_included"] == .bool(false), value["authenticity"] == .string("not_established"), value["content_warning"] == .string("selected_content_may_contain_secrets"), value["semantics"] == .string(isBackup ? "selected_paths_only_no_directory_recursion" : "new_unselected_workspace_no_execution") else { throw RPCFailure.invalidResponse }
        sourcePath = try RecoveryExtensionValidation.path(value["source_path"]); sourceIdentity = try MaintenanceFileIdentity(value["source_identity"])
        destinationParent = try RecoveryExtensionValidation.path(value["destination_parent"]); destinationIdentity = try MaintenanceFileIdentity(value["destination_identity"])
        destinationName = try RecoveryExtensionValidation.text(value["destination_name"], max: 96)
        guard MaintenanceValidation.name(destinationName) else { throw RPCFailure.invalidResponse }
        summary = try RecoveryWorkspaceSummary(value["summary"])
        selection = try ManagementValidation.rows(value["selection"], limit: 256, parse: RecoveryWorkspaceEntry.init)
        guard Set(selection.map(\.path)).count == selection.count, isBackup ? !selection.isEmpty : selection.isEmpty else { throw RPCFailure.invalidResponse }
        if isBackup {
            guard selection.filter({ $0.kind == "file" }).count == summary.files, selection.filter({ $0.kind == "file" && $0.role == "artifact" }).count == summary.artifacts else { throw RPCFailure.invalidResponse }
        }
    }
}

public struct RecoveryRepairDetails: Sendable {
    public let role: String
    public let repairKind: String
    public let destinationParent: String
    public let destinationName: String
    public let destinationIdentity: MaintenanceFileIdentity
    public let imageSHA: String
    public let imageBytes: Int
    public let sourceFingerprint: String
    public let sourceMode: String
    public let discardedBytes: Int?
    public let discardedFrameSlots: Int?
    public let derivedRows: Int?
    public let sources: [MaintenanceSource]
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["kind", "profile_id", "generation", "role", "repair_kind", "candidate", "source", "destination_parent", "destination_name", "destination_identity", "excluded", "original_preserved", "activation_included", "credentials_included", "semantics", "authenticity"])
        guard let role = value["role"].string, ["memory", "state", "schedules"].contains(role), let repair = value["repair_kind"].string, ["wal_valid_prefix", "memory_fts_rebuild"].contains(repair), repair != "memory_fts_rebuild" || role == "memory", value["original_preserved"] == .bool(true), value["activation_included"] == .bool(false), value["credentials_included"] == .bool(false), value["semantics"] == .string("new_repaired_backup_requires_separate_restore"), value["authenticity"] == .string("not_established") else { throw RPCFailure.invalidResponse }
        self.role = role; repairKind = repair
        destinationParent = try RecoveryExtensionValidation.path(value["destination_parent"]); destinationName = try RecoveryExtensionValidation.text(value["destination_name"], max: 96); destinationIdentity = try MaintenanceFileIdentity(value["destination_identity"])
        guard MaintenanceValidation.name(destinationName) else { throw RPCFailure.invalidResponse }
        _ = try MaintenanceValidation.strings(value["excluded"], exact: MaintenanceValidation.exclusions)
        sources = try ManagementValidation.rows(value["source"], limit: 7, parse: MaintenanceSource.init)
        guard Set(sources.map(\.role)).count == sources.count else { throw RPCFailure.invalidResponse }
        let row = value["candidate"]
        let base: Set<String> = ["kind", "role", "image_sha256", "image_bytes", "source_mode", "source_modified", "semantics", "source_fingerprint", "source_member_sha256"]
        let wal: Set<String> = ["pages", "page_size", "frames", "valid_frames", "committed_frames", "retained_bytes", "discarded_bytes", "discarded_frame_slots", "failure", "main_only"]
        let journal: Set<String> = ["pages", "page_size", "headers", "records", "applied", "inactive_bytes", "unsynced_records_not_applied"]
        let fts: Set<String> = ["base_rows_preserved", "derived_rows_before", "derived_rows_after", "derived_missing_or_mismatched_rows", "derived_extra_rows_removed", "base_data_loss"]
        guard let fields = row.object, Set(fields.keys).isSubset(of: base.union(wal).union(journal).union(fts).union(["lost_committed_transactions", "data_loss_possible"])), base.isSubset(of: Set(fields.keys)), row["kind"] == .string(repair), row["role"] == .string(role), row["source_modified"] == .bool(false), let mode = row["source_mode"].string, ["cold_delete", "cold_wal", "cold_wal_main_only", "cold_wal_empty", "cold_wal_header", "cold_journal", "cold_journal_hot", "cold_journal_inactive"].contains(mode) else { throw RPCFailure.invalidResponse }
        imageSHA = try RecoveryExtensionValidation.hash(row["image_sha256"]); imageBytes = try MaintenanceValidation.integer(row["image_bytes"], max: 67_108_864)
        sourceFingerprint = try RecoveryExtensionValidation.hash(row["source_fingerprint"]); sourceMode = mode
        guard let hashes = row["source_member_sha256"].object, hashes.count <= 4, hashes["main"] != nil, Set(hashes.keys).isSubset(of: ["main", "-wal", "-shm", "-journal"]) else { throw RPCFailure.invalidResponse }
        for hash in hashes.values { _ = try RecoveryExtensionValidation.hash(hash) }
        for key in wal.union(journal).union(fts).subtracting(["failure", "main_only", "applied", "base_data_loss", "base_rows_preserved"]) where fields[key] != nil { _ = try MaintenanceValidation.integer(row[key], max: 134_217_728) }
        if fields["main_only"] != nil { guard row["main_only"].bool != nil else { throw RPCFailure.invalidResponse } }
        if fields["applied"] != nil { guard row["applied"].bool != nil else { throw RPCFailure.invalidResponse } }
        if repair == "wal_valid_prefix" {
            guard wal.isSubset(of: Set(fields.keys)), row["semantics"] == .string("validated_committed_prefix_only"), row["data_loss_possible"] == .bool(true), row["lost_committed_transactions"] == .string("unknown"), row["main_only"] == .bool(false), let failure = row["failure"].string, ["checksum", "short_frame", "page_or_generation", "partial_frame"].contains(failure) else { throw RPCFailure.invalidResponse }
            discardedBytes = try MaintenanceValidation.integer(row["discarded_bytes"], max: 134_217_728); discardedFrameSlots = try MaintenanceValidation.integer(row["discarded_frame_slots"], max: 100_000)
            guard discardedBytes! > 0, row["committed_frames"].integer.map({ $0 > 0 }) == true else { throw RPCFailure.invalidResponse }; derivedRows = nil
        } else {
            guard fts.isSubset(of: Set(fields.keys)), row["semantics"] == .string("derived_index_rebuilt_from_preserved_base_rows"), row["base_data_loss"] == .bool(false), fields["data_loss_possible"] == nil, let counts = row["base_rows_preserved"].object, Set(counts.keys) == Set(["scopes", "documents", "chunks", "metadata"]) || Set(counts.keys) == Set(["scopes", "documents", "chunks", "metadata", "memory_scope_copies", "memory_scope_copy_documents"]) else { throw RPCFailure.invalidResponse }
            for number in counts.values { _ = try MaintenanceValidation.integer(number, max: 100_000) }
            derivedRows = try MaintenanceValidation.integer(row["derived_rows_after"], max: 100_000); discardedBytes = nil; discardedFrameSlots = nil
        }
    }
}

public struct RecoveryExtensionReview: Identifiable, Sendable {
    public let id: String
    public let digest: String
    public let kind: String
    public let scope: MaintenanceScope
    public let expiresAt: Date
    public let metadata: RPCValue
    public let workspace: RecoveryWorkspaceDetails?
    public let repair: RecoveryRepairDetails?
    public let release: RecoveryReleaseDetails?
    public var params: [String: RPCValue] { ["review_id": .string(id), "review_digest": .string(digest)] }
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "expires_in_seconds", "metadata"])
        guard let id = value["review_id"].string, ManagementValidation.identifier(id), let kind = value["metadata"]["kind"].string, RecoveryExtensionValidation.effects.contains(kind) else { throw RPCFailure.invalidResponse }
        self.id = id; self.kind = kind; digest = try RecoveryExtensionValidation.hash(value["review_digest"])
        metadata = value["metadata"]; scope = try MaintenanceScope(metadata)
        guard ManagementValidation.digest(try MaintenanceValidation.canonical(metadata)) == digest else { throw RPCFailure.invalidResponse }
        if kind.hasPrefix("workspace.") { workspace = try RecoveryWorkspaceDetails(metadata, kind: kind); repair = nil; release = nil }
        else if kind == "doctor.repair.apply" { workspace = nil; repair = try RecoveryRepairDetails(metadata); release = nil }
        else { workspace = nil; repair = nil; release = try RecoveryReleaseDetails(metadata) }
        expiresAt = now.addingTimeInterval(Double(try MaintenanceValidation.integer(value["expires_in_seconds"], max: 120)))
    }
    public func matches(_ operation: MaintenanceOperation) -> Bool { kind == operation.kind && scope == operation.scope && operation.reviewID == id && operation.reviewDigest == digest }
    public func matches(_ ticket: MaintenanceApproval, operation: MaintenanceOperation, now: Date = Date()) -> Bool {
        matches(operation) && operation.phase == "awaiting_owner" && ticket.operationID == operation.id && ticket.purpose == kind && ticket.scope == scope && ticket.reviewID == id && ticket.reviewDigest == digest && ticket.reviewMetadata == metadata && expiresAt > now && ticket.expiresAt > now
    }
}
