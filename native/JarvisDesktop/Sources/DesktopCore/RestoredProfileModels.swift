import Foundation

public struct RestoreResource: Identifiable, Sendable {
    public let role: String
    public let present: Bool
    public let bytes: Int
    public let counts: [(String, Int)]
    public var id: String { role }
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["role", "present", "sha256", "byte_length", "counts"])
        guard let role = value["role"].string, MaintenanceValidation.roles.contains(role), let present = value["present"].bool,
              let rows = value["counts"].array, rows.count <= 64 else { throw RPCFailure.invalidResponse }
        let bytes = try MaintenanceValidation.integer(value["byte_length"], max: 134_217_728)
        if present { guard let hash = value["sha256"].string, ManagementValidation.hash(hash), bytes > 0 else { throw RPCFailure.invalidResponse } }
        else { guard value["sha256"] == .null, bytes == 0, rows.isEmpty else { throw RPCFailure.invalidResponse } }
        var seen = Set<String>()
        counts = try rows.map { row in
            guard let pair = row.array, pair.count == 2, let name = pair[0].string,
                  name.range(of: "^[a-z][a-z0-9_]{0,63}\\z", options: .regularExpression) != nil,
                  seen.insert(name).inserted else { throw RPCFailure.invalidResponse }
            return (name, try MaintenanceValidation.integer(pair[1], max: 100_000))
        }
        self.role = role; self.present = present; self.bytes = bytes
    }
}

public struct RestoreProfileReview: Identifiable, Sendable {
    public let id: String
    public let digest: String
    public let scope: MaintenanceScope
    public let path: String
    public let identity: MaintenanceFileIdentity
    public let markerHash: String
    public let restoredID: String
    public let resources: [RestoreResource]
    public let metadata: RPCValue
    public let expiresAt: Date
    public var params: [String: RPCValue] { ["review_id": .string(id), "review_digest": .string(digest)] }
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "expires_in_seconds", "metadata"])
        guard let id = value["review_id"].string, ManagementValidation.identifier(id), let hash = value["review_digest"].string, ManagementValidation.hash(hash) else { throw RPCFailure.invalidResponse }
        let metadata = value["metadata"]
        try MaintenanceValidation.keys(metadata, ["kind", "profile_id", "generation", "root", "root_identity", "marker_sha256", "restored_profile_id", "source_manifest_sha256", "resource_fingerprint", "resources", "policy", "workspace_policy", "credentials_included", "authenticity"])
        guard metadata["kind"] == .string("profile.restore.activate"), let path = metadata["root"].string, MaintenanceValidation.path(path),
              let marker = metadata["marker_sha256"].string, ManagementValidation.hash(marker),
              let restored = metadata["restored_profile_id"].string, ManagementValidation.identifier(restored),
              let manifest = metadata["source_manifest_sha256"].string, ManagementValidation.hash(manifest),
              let fingerprint = metadata["resource_fingerprint"].string, ManagementValidation.hash(fingerprint),
              metadata["policy"] == .string("local_admission_only_no_automatic_effects"), metadata["workspace_policy"] == .string("new_private_empty"),
              metadata["credentials_included"] == .bool(false), metadata["authenticity"] == .string("not_established"),
              ManagementValidation.digest(try MaintenanceValidation.canonical(metadata)) == hash else { throw RPCFailure.invalidResponse }
        let resources = try ManagementValidation.rows(metadata["resources"], limit: 7, parse: RestoreResource.init)
        guard resources.count == 7, Set(resources.map(\.role)) == MaintenanceValidation.roles,
              resources.reduce(0, { $0 + $1.bytes }) <= 134_217_728 else { throw RPCFailure.invalidResponse }
        let lifetime = try MaintenanceValidation.integer(value["expires_in_seconds"], max: 120)
        self.id = id; digest = hash; self.metadata = metadata; self.path = path; markerHash = marker; restoredID = restored
        scope = try MaintenanceScope(metadata); identity = try MaintenanceFileIdentity(metadata["root_identity"]); self.resources = resources
        expiresAt = now.addingTimeInterval(Double(lifetime))
    }
    public func matches(_ operation: MaintenanceOperation) -> Bool {
        operation.kind == "profile.restore.activate" && operation.scope == scope && operation.reviewID == id && operation.reviewDigest == digest
    }
    public func matches(_ approval: MaintenanceApproval, operation: MaintenanceOperation, now: Date = Date()) -> Bool {
        matches(operation) && approval.operationID == operation.id && approval.scope == scope && approval.purpose == operation.kind && approval.reviewID == id && approval.reviewDigest == digest && approval.reviewMetadata == metadata && approval.expiresAt > now
    }
}

public struct RestoreActivationReceipt: Sendable {
    public let operationID: String
    public let status: String
    public let effect: String
    public let identity: MaintenanceFileIdentity?
    public let markerHash: String?
    public let admissionHash: String?
    public let cleanupPending: Bool
    public init(_ value: RPCValue, operationID: String) throws {
        try MaintenanceValidation.keys(value, ["operation_id", "purpose", "status", "effect_status", "target_identity", "marker_sha256", "admission_sha256", "error_code", "cleanup_pending", "cleanup_error_code", "services_started", "grants_restored"])
        guard value["operation_id"] == .string(operationID), value["purpose"] == .string("profile.restore.activate"),
              let status = value["status"].string, ["completed", "failed", "cancelled", "unknown"].contains(status),
              let effect = value["effect_status"].string, ["local_admission_published", "not_activated", "publication_unknown"].contains(effect),
              let cleanup = value["cleanup_pending"].bool, value["services_started"] == .bool(false), value["grants_restored"] == .bool(false) else { throw RPCFailure.invalidResponse }
        let identity = value["target_identity"] == .null ? nil : try MaintenanceFileIdentity(value["target_identity"])
        for key in ["marker_sha256", "admission_sha256"] { if value[key] != .null { guard let hash = value[key].string, ManagementValidation.hash(hash) else { throw RPCFailure.invalidResponse } } }
        _ = try MaintenanceValidation.code(value["error_code"]); _ = try MaintenanceValidation.code(value["cleanup_error_code"])
        if status == "completed" { guard effect == "local_admission_published", !cleanup, identity != nil, value["marker_sha256"] != .null, value["admission_sha256"] != .null, value["error_code"] == .null, value["cleanup_error_code"] == .null else { throw RPCFailure.invalidResponse } }
        self.operationID = operationID; self.status = status; self.effect = effect; self.identity = identity; markerHash = value["marker_sha256"].string; admissionHash = value["admission_sha256"].string; cleanupPending = cleanup
    }
}

public struct RestoredLocalAdmission: Sendable {
    public let identity: MaintenanceFileIdentity
    public let markerHash: String
    public let admissionHash: String
    public let operationID: String
    public let restoredID: String
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["root_identity", "marker_sha256", "restored_profile_id", "source_manifest_sha256", "admission_sha256", "operation_id", "initial_review_fingerprint", "state", "authenticity", "prior_operation_durability", "grants_restored"])
        guard value["state"] == .string("local_present_valid"), value["authenticity"] == .string("not_established"), value["prior_operation_durability"] == .string("not_established"), value["grants_restored"] == .bool(false),
              let marker = value["marker_sha256"].string, ManagementValidation.hash(marker), let admission = value["admission_sha256"].string, ManagementValidation.hash(admission),
              let operation = value["operation_id"].string, ManagementValidation.identifier(operation), let restored = value["restored_profile_id"].string, ManagementValidation.identifier(restored),
              let source = value["source_manifest_sha256"].string, ManagementValidation.hash(source), let fingerprint = value["initial_review_fingerprint"].string, ManagementValidation.hash(fingerprint) else { throw RPCFailure.invalidResponse }
        identity = try MaintenanceFileIdentity(value["root_identity"]); markerHash = marker; admissionHash = admission; operationID = operation; restoredID = restored
    }
}

public struct RestoreProfileInspection: Sendable {
    public let path: String
    public let identity: MaintenanceFileIdentity
    public let state: String
    public let admission: RestoredLocalAdmission?
    public let cleanupOwnerPending: Bool
    public let openAvailable: Bool
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["profile_path", "root_identity", "state", "admission", "cleanup_owner_pending", "open_available"])
        guard let path = value["profile_path"].string, MaintenanceValidation.path(path), let state = value["state"].string, ["local_present_valid", "activation_required", "not_restored_profile"].contains(state),
              let pending = value["cleanup_owner_pending"].bool, let available = value["open_available"].bool else { throw RPCFailure.invalidResponse }
        identity = try MaintenanceFileIdentity(value["root_identity"])
        admission = value["admission"] == .null ? nil : try RestoredLocalAdmission(value["admission"])
        guard (state == "local_present_valid") == (admission != nil), admission == nil || admission?.identity == identity,
              !available || (state == "local_present_valid" && !pending) else { throw RPCFailure.invalidResponse }
        self.path = path; self.state = state; cleanupOwnerPending = pending; openAvailable = available
    }
}

/// A constrained destination, never permission or proof of a previous operation's ACK.
public struct ProfileOpenTarget: Equatable, Sendable {
    public let path: String
    public let identity: MaintenanceFileIdentity
    public let markerHash: String
    public let admissionHash: String
    public let operationID: String
    public let followsUnconfirmedOperation: Bool
    public init(review: RestoreProfileReview, operation: MaintenanceOperation) throws {
        guard review.matches(operation), operation.phase == "activated_not_opened", operation.settled, operation.receiptDurable,
              !operation.cleanupOwnerPending, let receipt = operation.activationReceipt, receipt.status == "completed", receipt.effect == "local_admission_published", !receipt.cleanupPending,
              receipt.operationID == operation.id, receipt.identity == review.identity, receipt.markerHash == review.markerHash, let hash = receipt.admissionHash else { throw RPCFailure.configuration }
        path = review.path; identity = review.identity; markerHash = review.markerHash; admissionHash = hash; operationID = operation.id; followsUnconfirmedOperation = false
    }
    public init(inspection: RestoreProfileInspection) throws {
        guard inspection.openAvailable, !inspection.cleanupOwnerPending, let admission = inspection.admission else { throw RPCFailure.configuration }
        path = inspection.path; identity = inspection.identity; markerHash = admission.markerHash; admissionHash = admission.admissionHash; operationID = admission.operationID
        followsUnconfirmedOperation = true
    }
}

public struct LocalProfileStatus: Sendable {
    public let scope: MaintenanceScope
    public let identity: MaintenanceFileIdentity
    public let admission: RestoredLocalAdmission?
    public let providerQueryAllowed: Bool
    public let providerSelected: Bool
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["profile_id", "generation", "root_identity", "restore_admission", "provider_choice"])
        scope = try MaintenanceScope(value); identity = try MaintenanceFileIdentity(value["root_identity"])
        admission = value["restore_admission"] == .null ? nil : try RestoredLocalAdmission(value["restore_admission"])
        try MaintenanceValidation.keys(value["provider_choice"], ["required", "selected", "query_status_allowed", "revision"])
        guard let required = value["provider_choice"]["required"].bool, let selected = value["provider_choice"]["selected"].bool, let allowed = value["provider_choice"]["query_status_allowed"].bool,
              !required || selected || !allowed, admission == nil || admission?.identity == identity else { throw RPCFailure.invalidResponse }
        _ = try MaintenanceValidation.integer(value["provider_choice"]["revision"])
        providerQueryAllowed = allowed; providerSelected = selected
    }
    public func matches(_ target: ProfileOpenTarget) -> Bool {
        identity == target.identity && admission?.identity == target.identity && admission?.markerHash == target.markerHash && admission?.admissionHash == target.admissionHash && admission?.operationID == target.operationID
    }
}
