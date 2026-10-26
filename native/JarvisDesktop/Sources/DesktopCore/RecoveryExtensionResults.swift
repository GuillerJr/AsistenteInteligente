import Foundation

public struct RecoveryInstallationSummary: Sendable {
    public let notice: String
    public init(_ result: RPCValue) throws {
        try MaintenanceValidation.keys(result, ["release_installation"])
        let row = result["release_installation"]
        try MaintenanceValidation.keys(row, ["installation", "installation_sha256", "current", "current_sha256", "pending", "pending_sha256", "temporary_current_files", "root_identity", "mutation_performed", "launch_performed", "automatic_replay", "prior_publication_durability", "release_payload_verification"])
        _ = try MaintenanceFileIdentity(row["root_identity"])
        guard ["mutation_performed", "launch_performed", "automatic_replay"].allSatisfy({ row[$0] == .bool(false) }), row["prior_publication_durability"] == .string("not_established"), row["release_payload_verification"] == .string("not_performed_by_control_readback") else { throw RPCFailure.invalidResponse }
        for key in ["installation_sha256", "current_sha256", "pending_sha256"] where row[key] != .null { _ = try RecoveryExtensionValidation.hash(row[key]) }
        guard let temporary = row["temporary_current_files"].array, temporary.count <= 16 else { throw RPCFailure.invalidResponse }
        for file in temporary { _ = try RecoveryExtensionValidation.text(file, max: 128) }
        if row["installation"] != .null {
            try MaintenanceValidation.keys(row["installation"], ["format", "installation_id", "channel"])
            guard row["installation"]["format"] == .string("jarvis-local-installation/1"), row["installation"]["channel"] == .string("development"), let id = row["installation"]["installation_id"].string, ManagementValidation.identifier(id) else { throw RPCFailure.invalidResponse }
        }
        if row["current"] != .null {
            let current = row["current"]
            try MaintenanceValidation.keys(current, ["format", "revision", "operation_id", "release_key", "manifest_sha256", "previous", "profile_pin"])
            guard current["format"] == .string("jarvis-local-installation/1"), row["current_sha256"] != .null else { throw RPCFailure.invalidResponse }
            let key = try RecoveryReleaseDetails.key(current["release_key"])
            _ = try RecoveryExtensionValidation.hash(current["manifest_sha256"]); try RecoveryReleaseDetails.profilePin(current["profile_pin"])
            notice = row["pending"] == .null ? "Versión seleccionada localmente: \(key). Esta lectura no la ejecutó." : "Hay una publicación pendiente sin resolver. No se cambiará la versión ni se repetirá la operación."
        } else { notice = row["pending"] == .null ? "Todavía no hay una versión seleccionada en esta instalación local." : "Hay una publicación pendiente sin confirmar; no se ha elegido otra versión." }
    }
}

public extension RecoveryExtensionValidation {
    static func validateResult(kind: String, result: RPCValue, phase: String, receiptDurable: Bool, hasReceipt: Bool) throws {
        if reads.contains(kind) {
            guard !receiptDurable, !hasReceipt else { throw RPCFailure.invalidResponse }
            guard result != .null else { if phase == "complete" { throw RPCFailure.invalidResponse }; return }
            if kind == "release.inspect" { _ = try RecoveryInstallationSummary(result) }
            else if kind == "release.resolve_launch" { _ = try RecoveryLaunchPlan(result) }
            else {
                try MaintenanceValidation.keys(result, ["review"])
                let review = try RecoveryExtensionReview(result["review"])
                let expected = ["workspace.backup.review":"workspace.backup.create", "workspace.restore.review":"workspace.restore.stage", "doctor.repair.review":"doctor.repair.apply", "release.review":"release.update.apply", "release.rollback.review":"release.rollback.apply"]
                guard review.kind == expected[kind] else { throw RPCFailure.invalidResponse }
            }
            return
        }
        guard effects.contains(kind) else { throw RPCFailure.invalidResponse }
        if phase == "complete" { guard receiptDurable, hasReceipt, result != .null else { throw RPCFailure.invalidResponse } }
        if result == .null { return }
        if kind.hasPrefix("workspace.") {
            try MaintenanceValidation.keys(result, ["workspace", "association_changed"])
            guard result["association_changed"] == .bool(false) else { throw RPCFailure.invalidResponse }
            let row = result["workspace"]
            try MaintenanceValidation.keys(row, ["target_identity", "manifest_sha256", "file_count", "directory_count", "total_bytes", "effect_status", "activation_required", "replay_allowed"])
            _ = try MaintenanceFileIdentity(row["target_identity"]); _ = try hash(row["manifest_sha256"])
            _ = try MaintenanceValidation.integer(row["file_count"], max: 256); _ = try MaintenanceValidation.integer(row["directory_count"], max: 128); _ = try MaintenanceValidation.integer(row["total_bytes"], max: 33_554_432)
            let backup = kind == "workspace.backup.create"
            guard row["effect_status"] == .string(backup ? "new_workspace_archive" : "new_unselected_workspace"), row["activation_required"] == .bool(!backup), row["replay_allowed"] == .bool(false) else { throw RPCFailure.invalidResponse }
        } else if kind == "doctor.repair.apply" {
            try MaintenanceValidation.keys(result, ["repair", "restoration_required", "original_preserved"])
            guard result["restoration_required"] == .bool(true), result["original_preserved"] == .bool(true), let repair = result["repair"].object, repair["source_modified"] == .bool(false) else { throw RPCFailure.invalidResponse }
            _ = try hash(result["repair"]["image_sha256"])
        } else {
            try MaintenanceValidation.keys(result, ["release"])
            let row = result["release"]
            try MaintenanceValidation.keys(row, ["operation_id", "kind", "status", "effect_status", "review_digest", "version_key", "manifest_sha256", "previous_key", "current_sha256", "profile_pin", "error_code", "cleanup_pending", "release_authenticity", "launch_performed", "profile_data_modified", "automatic_replay", "restored_profile_activation_requires_owner"])
            guard row["kind"] == .string(kind == "release.update.apply" ? "release.update" : "release.rollback"), row["release_authenticity"] == .string("not_established"), ["launch_performed", "profile_data_modified", "automatic_replay"].allSatisfy({ row[$0] == .bool(false) }), row["restored_profile_activation_requires_owner"] == .bool(true), row["cleanup_pending"].bool != nil else { throw RPCFailure.invalidResponse }
            _ = try hash(row["review_digest"]); _ = try hash(row["manifest_sha256"]); _ = try RecoveryReleaseDetails.key(row["version_key"])
            if row["current_sha256"] != .null { _ = try hash(row["current_sha256"]) }
            try RecoveryReleaseDetails.profilePin(row["profile_pin"])
            if phase == "complete" { guard row["status"] == .string("completed"), row["effect_status"] == .string("current_selected"), row["current_sha256"] != .null, row["cleanup_pending"] == .bool(false) else { throw RPCFailure.invalidResponse } }
        }
    }
}

public extension RecoveryExtensionReview {
    func validateResult(_ operation: MaintenanceOperation) throws {
        guard matches(operation) else { throw RPCFailure.invalidResponse }
        try RecoveryExtensionValidation.validateResult(kind: kind, result: operation.result, phase: operation.phase, receiptDurable: operation.receiptDurable, hasReceipt: operation.receipt != nil)
        if operation.result == .null { return }
        if let workspace {
            let row = operation.result["workspace"]
            guard row["manifest_sha256"] == .string(workspace.summary.manifestSHA), row["file_count"].integer == workspace.summary.files, row["directory_count"].integer == workspace.summary.directories, row["total_bytes"].integer == workspace.summary.bytes else { throw RPCFailure.invalidResponse }
            if let receipt = operation.receipt { guard receipt.targetName == workspace.destinationName, receipt.manifestHash == nil || receipt.manifestHash == workspace.summary.manifestSHA else { throw RPCFailure.invalidResponse } }
        } else if repair != nil { guard operation.result["repair"] == metadata["candidate"] else { throw RPCFailure.invalidResponse } }
        else if let release {
            let row = operation.result["release"]
            guard row["operation_id"] == .string(operation.id), row["manifest_sha256"] == .string(release.manifestSHA), row["version_key"] == .string(release.versionKey), row["previous_key"] == metadata["previous_key"], row["review_digest"] == metadata["digest"], row["profile_pin"] == metadata["profile_pin"] else { throw RPCFailure.invalidResponse }
            if let receipt = operation.receipt { guard receipt.targetName == release.versionKey, receipt.manifestHash == nil || receipt.manifestHash == release.manifestSHA else { throw RPCFailure.invalidResponse } }
        }
    }
}
