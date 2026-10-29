import Foundation

public struct RecoveryReleaseDetails: Sendable {
    public let artifactPath: String
    public let installRoot: String
    public let versionKey: String
    public let manifestSHA: String
    public let previousKey: String?
    public let files: Int
    public let bytes: Int
    public let backupPath: String
    public let backupSHA: String
    public let signatureEvidence: String
    init(_ row: RPCValue) throws {
        try MaintenanceValidation.keys(row, ["kind", "profile_id", "generation", "artifact_root", "install_root", "version_key", "manifest_sha256", "expected_current_sha256", "expected_installation_sha256", "previous_key", "profile_pin", "channel", "release_authenticity", "platform_evidence", "external_dependencies", "entrypoints", "files", "bytes", "launch_performed", "profile_mutation_performed", "digest", "source", "provider_used", "backup_created_by_this_operation", "recovery_copy", "source_profile_and_recovery_copy_match", "profile_data_modified", "credentials_included"])
        guard let kind = row["kind"].string, ["release.update.apply", "release.rollback.apply"].contains(kind), row["channel"] == .string("development"), row["release_authenticity"] == .string("not_established"), ["launch_performed", "profile_mutation_performed", "provider_used", "backup_created_by_this_operation", "profile_data_modified", "credentials_included"].allSatisfy({ row[$0] == .bool(false) }), row["source_profile_and_recovery_copy_match"] == .bool(true) else { throw RPCFailure.invalidResponse }
        artifactPath = try RecoveryExtensionValidation.path(row["artifact_root"]); installRoot = try RecoveryExtensionValidation.path(row["install_root"])
        versionKey = try Self.key(row["version_key"]); previousKey = row["previous_key"] == .null ? nil : try Self.key(row["previous_key"])
        manifestSHA = try RecoveryExtensionValidation.hash(row["manifest_sha256"]); _ = try RecoveryExtensionValidation.hash(row["digest"])
        for key in ["expected_current_sha256", "expected_installation_sha256"] where row[key] != .null { _ = try RecoveryExtensionValidation.hash(row[key]) }
        files = try MaintenanceValidation.integer(row["files"], max: 2048); bytes = try MaintenanceValidation.integer(row["bytes"], max: 268_435_456)
        guard files > 0 else { throw RPCFailure.invalidResponse }
        try MaintenanceValidation.keys(row["external_dependencies"], ["python", "codex"])
        guard row["external_dependencies"]["python"] == .string("external_required"), row["external_dependencies"]["codex"] == .string("external_official_required") else { throw RPCFailure.invalidResponse }
        try MaintenanceValidation.keys(row["entrypoints"], ["python", "native"])
        guard row["entrypoints"]["python"] == .string("python/jarvis_core/service.py"), row["entrypoints"]["native"] == .string("app/Jarvis.app/Contents/MacOS/Jarvis") else { throw RPCFailure.invalidResponse }
        _ = try Self.profilePin(row["profile_pin"], scope: MaintenanceScope(row))
        let sources = try ManagementValidation.rows(row["source"], limit: MaintenanceValidation.roles.count - 1, parse: MaintenanceSource.init)
        guard Set(sources.map(\.role)).count == sources.count,
              MaintenanceValidation.acceptsRoles(Set(sources.map(\.role)), includesConfiguration: false) else { throw RPCFailure.invalidResponse }
        let copy = row["recovery_copy"]
        try MaintenanceValidation.keys(copy, ["path", "manifest_sha256", "artifact_id", "root_identity"])
        backupPath = try RecoveryExtensionValidation.path(copy["path"]); backupSHA = try RecoveryExtensionValidation.hash(copy["manifest_sha256"])
        guard let artifact = copy["artifact_id"].string, ManagementValidation.identifier(artifact), copy["manifest_sha256"] == row["profile_pin"]["backup_manifest_sha256"] else { throw RPCFailure.invalidResponse }
        _ = try MaintenanceFileIdentity(copy["root_identity"])
        guard let evidence = row["platform_evidence"].object, !evidence.isEmpty, evidence.count <= 8 else { throw RPCFailure.invalidResponse }
        for (key, value) in evidence {
            guard key.utf8.count <= 128, value.bool != nil || value.string != nil else { throw RPCFailure.invalidResponse }
            if value.string != nil { _ = try RecoveryExtensionValidation.text(value) }
        }
        signatureEvidence = try MaintenanceValidation.canonical(row["platform_evidence"])
    }
    static func key(_ value: RPCValue) throws -> String {
        let text = try RecoveryExtensionValidation.text(value, max: 64)
        guard text.range(of: "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }; return text
    }
    @discardableResult static func profilePin(_ row: RPCValue, scope: MaintenanceScope? = nil) throws -> MaintenanceScope {
        try MaintenanceValidation.keys(row, ["profile_id", "generation", "root_identity", "source_fingerprint", "schemas", "maintenance_purposes", "backup_manifest_sha256", "restored_profile_activation_requires_owner", "profile_data_modified", "automatic_replay"])
        let parsed = try MaintenanceScope(row)
        guard scope == nil || scope == parsed, row["restored_profile_activation_requires_owner"] == .bool(true), row["profile_data_modified"] == .bool(false), row["automatic_replay"] == .bool(false) else { throw RPCFailure.invalidResponse }
        _ = try MaintenanceFileIdentity(row["root_identity"]); _ = try RecoveryExtensionValidation.hash(row["source_fingerprint"]); _ = try RecoveryExtensionValidation.hash(row["backup_manifest_sha256"])
        _ = try maintenancePurposes(row["maintenance_purposes"])
        try MaintenanceValidation.keys(row["schemas"], ["state", "memory", "schedules"])
        for key in ["state", "memory", "schedules"] { _ = try MaintenanceValidation.integer(row["schemas"][key], max: 2_147_483_647) }; return parsed
    }
    static func maintenancePurposes(_ value: RPCValue) throws -> [String] {
        let allowed: Set<String> = ["backup.create", "restore.stage", "profile.restore.activate", "memory.restore.copy_scope", "doctor.repair.apply", "workspace.backup.create", "workspace.restore.stage", "release.update.apply", "release.rollback.apply", ChannelReconciliationValidation.effect]
        guard let values = value.array, values.count <= allowed.count else { throw RPCFailure.invalidResponse }
        let purposes = try values.map { value -> String in
            guard let text = value.string, allowed.contains(text) else { throw RPCFailure.invalidResponse }; return text
        }
        guard purposes == purposes.sorted(), Set(purposes).count == purposes.count, Set(["release.update.apply", "release.rollback.apply"]).isSubset(of: Set(purposes)) else { throw RPCFailure.invalidResponse }; return purposes
    }
}

public struct RecoveryLaunchPlan: Sendable {
    public let nativeExecutable: String
    public let pythonSource: String
    public let currentSHA: String
    public let manifestSHA: String
    public let versionKey: String
    public let maintenancePurposes: [String]
    public init(_ result: RPCValue) throws {
        try MaintenanceValidation.keys(result, ["release_launch"])
        let row = result["release_launch"]
        try MaintenanceValidation.keys(row, ["install_root", "release_root", "version_key", "manifest_sha256", "current_sha256", "native_executable", "python_source", "profile_pin", "maintenance_purposes", "payload_verified", "same_release_source_and_native", "release_authenticity", "channel", "external_python_required", "external_official_codex_required", "launch_performed", "restored_profile_activation_requires_owner"])
        guard ["payload_verified", "same_release_source_and_native", "external_python_required", "external_official_codex_required", "restored_profile_activation_requires_owner"].allSatisfy({ row[$0] == .bool(true) }), row["launch_performed"] == .bool(false), row["release_authenticity"] == .string("not_established"), row["channel"] == .string("development") else { throw RPCFailure.invalidResponse }
        let root = try RecoveryExtensionValidation.path(row["release_root"])
        let install = try RecoveryExtensionValidation.path(row["install_root"])
        versionKey = try RecoveryReleaseDetails.key(row["version_key"])
        guard root == install + "/releases/" + versionKey else { throw RPCFailure.invalidResponse }
        nativeExecutable = try RecoveryExtensionValidation.path(row["native_executable"]); pythonSource = try RecoveryExtensionValidation.path(row["python_source"])
        guard nativeExecutable == root + "/app/Jarvis.app/Contents/MacOS/Jarvis", pythonSource == root + "/python" else { throw RPCFailure.invalidResponse }
        currentSHA = try RecoveryExtensionValidation.hash(row["current_sha256"]); manifestSHA = try RecoveryExtensionValidation.hash(row["manifest_sha256"])
        try RecoveryReleaseDetails.profilePin(row["profile_pin"])
        maintenancePurposes = try RecoveryReleaseDetails.maintenancePurposes(row["maintenance_purposes"])
        let required = try RecoveryReleaseDetails.maintenancePurposes(row["profile_pin"]["maintenance_purposes"])
        guard Set(required).isSubset(of: Set(maintenancePurposes)) else { throw RPCFailure.invalidResponse }
    }
}
