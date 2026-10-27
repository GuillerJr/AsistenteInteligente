import Foundation

/// Local retrieval settings, never permission to open a source URI or a file.
public enum MemoryRecallMode: String, CaseIterable, Sendable {
    case lexical, semanticLocal = "semantic_local", hybridLocal = "hybrid_local"
    public var title: String {
        switch self {
        case .lexical: return "Texto exacto"
        case .semanticLocal: return "Semántica local"
        case .hybridLocal: return "Texto y semántica local"
        }
    }
}

public struct MemoryIntelligenceSettings: Equatable, Sendable {
    public var autoRecallEnabled: Bool
    public var mode: MemoryRecallMode
    public var autoIndexEnabled: Bool
    public init(autoRecallEnabled: Bool = false, mode: MemoryRecallMode = .lexical,
                autoIndexEnabled: Bool = false) {
        self.autoRecallEnabled = autoRecallEnabled; self.mode = mode
        self.autoIndexEnabled = autoIndexEnabled
    }
    public var value: RPCValue {
        .object(["auto_recall_enabled": .bool(autoRecallEnabled), "mode": .string(mode.rawValue),
                 "auto_index_enabled": .bool(autoIndexEnabled), "source_policy": .string("approved_notes_only")])
    }
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["auto_recall_enabled", "mode", "auto_index_enabled", "source_policy"])
        guard let recall = value["auto_recall_enabled"].bool,
              let mode = value["mode"].string.flatMap(MemoryRecallMode.init(rawValue:)),
              let index = value["auto_index_enabled"].bool,
              value["source_policy"] == .string("approved_notes_only") else { throw RPCFailure.invalidResponse }
        self.init(autoRecallEnabled: recall, mode: mode, autoIndexEnabled: index)
    }
}

enum MemoryIntelligenceValidation {
    static func hash(_ value: RPCValue) throws -> String {
        guard let text = value.string, ManagementValidation.hash(text) else { throw RPCFailure.invalidResponse }; return text
    }
    static func optionalHash(_ value: RPCValue) throws -> String? { value == .null ? nil : try hash(value) }
    static func identifier(_ value: RPCValue) throws -> String {
        guard let text = value.string, ManagementValidation.identifier(text) else { throw RPCFailure.invalidResponse }; return text
    }
    static func code(_ value: RPCValue, nullable: Bool = false) throws -> String? {
        guard value != .null else { if nullable { return nil }; throw RPCFailure.invalidResponse }
        guard let text = value.string, text.range(of: "^[a-z][a-z0-9_]{0,127}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }; return text
    }
    static func session(_ value: RPCValue) throws -> SessionScope {
        try MaintenanceValidation.keys(value, ["session_id", "temporary"])
        let session = try identifier(value["session_id"])
        guard let temporary = value["temporary"].bool else { throw RPCFailure.invalidResponse }
        return SessionScope(sessionID: session, temporary: temporary)
    }
    static func metadataObject(_ value: RPCValue) throws -> Bool {
        if value == .null { return false }
        guard value.object != nil, try MaintenanceValidation.canonical(value).utf8.count <= 16_384 else { throw RPCFailure.invalidResponse }
        return true
    }
}

public struct MemoryPolicyIdentity: Equatable, Sendable {
    public let clientID: String
    public let agentID: String
    public let workspaceID: String
    public let temporary: Bool
    public let sessionID: String?
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["client_id", "agent_id", "workspace_id", "temporary", "session_id"])
        clientID = try MemoryIntelligenceValidation.hash(value["client_id"])
        workspaceID = try MemoryIntelligenceValidation.hash(value["workspace_id"])
        guard let agent = value["agent_id"].string,
              agent.range(of: "^[A-Za-z0-9_-]{1,64}\\z", options: .regularExpression) != nil,
              let temporary = value["temporary"].bool else { throw RPCFailure.invalidResponse }
        agentID = agent; self.temporary = temporary
        sessionID = value["session_id"] == .null ? nil : try MemoryIntelligenceValidation.identifier(value["session_id"])
        guard temporary == (sessionID != nil) else { throw RPCFailure.invalidResponse }
    }
    public func matches(_ scope: SessionScope) -> Bool {
        temporary == scope.temporary && (!temporary || sessionID == scope.sessionID)
    }
}

public struct MemoryPolicySnapshot: Equatable, Sendable {
    public let profileBinding: String
    public let scope: MemoryPolicyIdentity
    public let revision: Int
    public let settings: MemoryIntelligenceSettings
    public let digest: String
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["profile_binding", "scope", "revision", "settings", "permissions_portable", "digest"])
        profileBinding = try MemoryIntelligenceValidation.hash(value["profile_binding"])
        scope = try MemoryPolicyIdentity(value["scope"])
        revision = try MaintenanceValidation.integer(value["revision"])
        settings = try MemoryIntelligenceSettings(value["settings"])
        digest = try MemoryIntelligenceValidation.hash(value["digest"])
        guard revision > 0, value["permissions_portable"] == .bool(false), var unsigned = value.object else { throw RPCFailure.invalidResponse }
        unsigned.removeValue(forKey: "digest")
        guard ManagementValidation.digest(try MaintenanceValidation.canonical(.object(unsigned))) == digest else { throw RPCFailure.invalidResponse }
    }
    public func sameOwner(as other: Self) -> Bool { profileBinding == other.profileBinding && scope == other.scope }
}

public struct MemoryPolicyChangeReview: Identifiable, Sendable {
    public let id: String
    public let digest: String
    public let beforeDigest: String
    public let beforeRevision: Int
    public let profileBinding: String
    public let scope: MemoryPolicyIdentity
    public let settings: MemoryIntelligenceSettings
    public let expiresAt: Date
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "expires_in_seconds", "metadata"])
        id = try MemoryIntelligenceValidation.identifier(value["review_id"])
        digest = try MemoryIntelligenceValidation.hash(value["review_digest"])
        let row = value["metadata"]
        try MaintenanceValidation.keys(row, ["kind", "review_id", "profile_binding", "scope", "before_revision", "before_digest", "settings", "source_resources_opened", "permissions_portable"])
        guard row["kind"] == .string("memory.policy.configure"), row["review_id"] == .string(id),
              row["source_resources_opened"] == .bool(false), row["permissions_portable"] == .bool(false),
              ManagementValidation.digest(try MaintenanceValidation.canonical(row)) == digest else { throw RPCFailure.invalidResponse }
        profileBinding = try MemoryIntelligenceValidation.hash(row["profile_binding"])
        scope = try MemoryPolicyIdentity(row["scope"])
        beforeRevision = try MaintenanceValidation.integer(row["before_revision"])
        beforeDigest = try MemoryIntelligenceValidation.hash(row["before_digest"])
        settings = try MemoryIntelligenceSettings(row["settings"])
        guard case .number(let ttl) = value["expires_in_seconds"], ttl.isFinite,
              ttl > 0, ttl <= 120, beforeRevision > 0 else { throw RPCFailure.invalidResponse }
        expiresAt = now.addingTimeInterval(ttl)
    }
    public func matches(_ policy: MemoryPolicySnapshot) -> Bool {
        profileBinding == policy.profileBinding && scope == policy.scope && beforeRevision == policy.revision && beforeDigest == policy.digest
    }
}

public struct MemoryPreviousModelCopy: Identifiable, Equatable, Sendable {
    public let id: String
    public let bytes: Int
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["id", "bytes"])
        id = try MemoryIntelligenceValidation.identifier(value["id"])
        bytes = try MaintenanceValidation.integer(value["bytes"], max: 256_000_000)
    }
}

public struct MemoryLocalModelStatus: Equatable, Sendable {
    public let id: String
    public let state: String
    public let available: Bool
    public let runtimeAvailable: Bool
    public let bytes: Int
    public let fingerprint: String?
    public let previousCopy: MemoryPreviousModelCopy?
    init(_ value: RPCValue) throws {
        let base: Set<String> = ["id", "state", "available", "runtime_available", "bytes", "fingerprint", "cpu_only", "network_for_inference"]
        guard let fields = value.object, Set(fields.keys) == base || Set(fields.keys) == base.union(["previous_copy"]) else { throw RPCFailure.invalidResponse }
        guard let id = value["id"].string, id.range(of: "^[A-Za-z0-9][A-Za-z0-9_./-]{0,127}\\z", options: .regularExpression) != nil,
              let available = value["available"].bool, let runtime = value["runtime_available"].bool,
              value["cpu_only"] == .bool(true), value["network_for_inference"] == .bool(false) else { throw RPCFailure.invalidResponse }
        self.id = id; self.available = available; runtimeAvailable = runtime
        state = try MemoryIntelligenceValidation.code(value["state"])!
        bytes = try MaintenanceValidation.integer(value["bytes"], max: 256_000_000)
        fingerprint = try MemoryIntelligenceValidation.optionalHash(value["fingerprint"])
        previousCopy = value["previous_copy"] == .null ? nil : try MemoryPreviousModelCopy(value["previous_copy"])
        guard !available || fingerprint != nil else { throw RPCFailure.invalidResponse }
    }
}

public struct MemoryLocalIndexStatus: Equatable, Sendable {
    public let state: String
    public let sourceGeneration: Int
    public let generation: Int?
    public let documentCount: Int
    public let chunkCount: Int
    public let bytes: Int
    public let modelFingerprint: String?
    public let error: String?
    public var isOpened: Bool { state != "not_opened" }
    init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["state", "source_generation", "generation", "document_count", "chunk_count", "bytes", "model_fingerprint", "error"])
        state = try MemoryIntelligenceValidation.code(value["state"])!
        sourceGeneration = try MaintenanceValidation.integer(value["source_generation"])
        generation = value["generation"] == .null ? nil : try MaintenanceValidation.integer(value["generation"])
        documentCount = try MaintenanceValidation.integer(value["document_count"], max: 100_000)
        chunkCount = try MaintenanceValidation.integer(value["chunk_count"], max: 1_000_000)
        bytes = try MaintenanceValidation.integer(value["bytes"], max: 268_435_456)
        modelFingerprint = try MemoryIntelligenceValidation.optionalHash(value["model_fingerprint"])
        error = try MemoryIntelligenceValidation.code(value["error"], nullable: true)
        if state == "not_opened" {
            guard sourceGeneration == 0, generation == nil, documentCount == 0, chunkCount == 0,
                  bytes == 0, modelFingerprint == nil, error == nil else { throw RPCFailure.invalidResponse }
        }
    }
}

public enum MemoryIntelligenceJobKind: String, Sendable {
    case modelPrepare = "model_prepare", modelRetire = "model_retire", indexRebuild = "index_rebuild", indexAuto = "index_auto"
}
public enum MemoryIntelligenceJobPhase: String, Sendable {
    case queued, running, cancelling, complete, failed, cancelled, cleanupPending = "cleanup_pending"
    public var settled: Bool { [.complete, .failed, .cancelled].contains(self) }
}
public struct MemoryIntelligenceJob: Identifiable, Equatable, Sendable {
    public let id: String
    public let kind: MemoryIntelligenceJobKind
    public let phase: MemoryIntelligenceJobPhase
    public let error: String?
    public let sourceGeneration: Int?
    public let policyRevision: Int
    public let documentsDone: Int
    public let documentsTotal: Int
    public let chunks: Int
    /// A receipt is retained by the backend. The panel stores no raw receipt or note content.
    public let hasReceipt: Bool
    public var settled: Bool { phase.settled }
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["id", "kind", "phase", "error", "source_generation", "policy_revision", "progress", "receipt"])
        id = try MemoryIntelligenceValidation.identifier(value["id"])
        guard let kind = value["kind"].string.flatMap(MemoryIntelligenceJobKind.init(rawValue:)),
              let phase = value["phase"].string.flatMap(MemoryIntelligenceJobPhase.init(rawValue:)) else { throw RPCFailure.invalidResponse }
        self.kind = kind; self.phase = phase
        error = try MemoryIntelligenceValidation.code(value["error"], nullable: true)
        sourceGeneration = value["source_generation"] == .null ? nil : try MaintenanceValidation.integer(value["source_generation"])
        policyRevision = try MaintenanceValidation.integer(value["policy_revision"])
        guard policyRevision > 0 else { throw RPCFailure.invalidResponse }
        try MaintenanceValidation.keys(value["progress"], ["documents_done", "documents_total", "chunks"])
        documentsDone = try MaintenanceValidation.integer(value["progress"]["documents_done"], max: 100_000)
        documentsTotal = try MaintenanceValidation.integer(value["progress"]["documents_total"], max: 100_000)
        chunks = try MaintenanceValidation.integer(value["progress"]["chunks"], max: 1_000_000)
        hasReceipt = try MemoryIntelligenceValidation.metadataObject(value["receipt"])
        guard documentsDone <= documentsTotal else { throw RPCFailure.invalidResponse }
    }
    public func follows(_ before: Self) -> Bool {
        id == before.id && kind == before.kind && policyRevision == before.policyRevision && sourceGeneration == before.sourceGeneration && (!before.settled || self == before)
    }
}

public struct MemoryIntelligenceStatus: Equatable, Sendable {
    public let scope: SessionScope
    public let policy: MemoryPolicySnapshot
    public let model: MemoryLocalModelStatus
    public let index: MemoryLocalIndexStatus
    public let currentJob: MemoryIntelligenceJob?
    public let lastRecall: MemoryRecallSummary?
    public init(_ envelope: RPCValue, expectedScope: SessionScope) throws {
        try MaintenanceValidation.keys(envelope, ["intelligence"])
        let value = envelope["intelligence"]
        try MaintenanceValidation.keys(value, ["scope", "policy", "model", "index", "current_job", "last_recall"])
        scope = try MemoryIntelligenceValidation.session(value["scope"])
        guard scope == expectedScope else { throw RPCFailure.invalidResponse }
        policy = try MemoryPolicySnapshot(value["policy"])
        guard policy.scope.matches(scope) else { throw RPCFailure.invalidResponse }
        model = try MemoryLocalModelStatus(value["model"]); index = try MemoryLocalIndexStatus(value["index"])
        currentJob = value["current_job"] == .null ? nil : try MemoryIntelligenceJob(value["current_job"])
        lastRecall = value["last_recall"] == .null ? nil : try MemoryRecallSummary(value["last_recall"])
    }
}

/// Deliberately drops additional server fields; no retrieved text is retained by this panel.
public struct MemoryRecallSummary: Equatable, Sendable {
    public let status: String
    public let mode: MemoryRecallMode
    public let citations: Int
    public let queryProjected: Bool
    public let error: String?
    public let runID: String?
    init(_ value: RPCValue) throws {
        guard let fields = value.object,
              Set(["status", "mode", "citations", "query_projected", "error", "run_id"]).isSubset(of: Set(fields.keys)),
              let status = value["status"].string, ["disabled", "empty", "ready", "unavailable"].contains(status),
              let mode = value["mode"].string.flatMap(MemoryRecallMode.init(rawValue:)),
              let projected = value["query_projected"].bool else { throw RPCFailure.invalidResponse }
        self.status = status; self.mode = mode; queryProjected = projected
        citations = try MaintenanceValidation.integer(value["citations"], max: 100)
        error = try MemoryIntelligenceValidation.code(value["error"], nullable: true)
        runID = value["run_id"] == .null ? nil : try MemoryIntelligenceValidation.identifier(value["run_id"])
    }
    public func notice(for expectedRunID: String?) -> String? {
        guard let expectedRunID, runID == expectedRunID, status != "disabled" else { return nil }
        if status == "unavailable" {
            return "La memoria local no estuvo disponible en este turno. Jarvis continuó sin añadir recuerdos; revisa Memoria."
        }
        if queryProjected {
            return "La búsqueda de memoria usó una consulta acotada; el mensaje original de la conversación se conserva."
        }
        return nil
    }
}

public struct MemoryIndexVerification: Equatable, Sendable {
    public let verified: Bool
    public let generation: Int
    public let fingerprint: String?
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["verified", "generation", "index_fingerprint"])
        guard let verified = value["verified"].bool else { throw RPCFailure.invalidResponse }; self.verified = verified
        generation = try MaintenanceValidation.integer(value["generation"])
        fingerprint = try MemoryIntelligenceValidation.optionalHash(value["index_fingerprint"])
        guard !verified || fingerprint != nil else { throw RPCFailure.invalidResponse }
    }
}
