import Foundation

/// The authenticated local settings catalogue contains metadata, never credentials.
public struct JarvisExtensionsSnapshot: Decodable, Sendable {
    public struct PolicyStatus: Decodable, Sendable {
        public let valid: Bool
        public let state: String
    }
    public let skills: [JarvisExtensionSkill]
    public let plugins: [JarvisExtensionPlugin]
    public let tool_options: [String: [String]]
    public let busy: Bool
    public let restart_required: Bool
    public let policy_status: PolicyStatus?
}

public struct JarvisExtensionSkill: Decodable, Identifiable, Sendable {
    public let skill_id: String
    public let name: String
    public let description: String
    public let role: String
    public let origin: String
    public let content_sha256: String
    public let enabled: Bool
    public let remote_allowed: Bool
    public let remote_authorized: Bool?
    public let builtin_public: Bool
    public let approval_required: Bool
    public let allowed_tools: [String]
    public let starter_tools: [String]
    public let preview_available: Bool
    public let effective_enabled: Bool?
    public let loaded: Bool?
    public let restart_required: Bool?
    public let plugin_id: String?
    public let adaptation_required: Bool?
    public var id: String { skill_id }
    public var active: Bool { effective_enabled ?? enabled }
    public var sharingAuthorized: Bool { remote_authorized ?? remote_allowed }
    public var originTitle: String {
        switch origin {
        case "builtin": "Integrada"
        case "plugin": "Plugin"
        default: "Personal"
        }
    }
    public var statusTitle: String {
        if adaptation_required == true { return "Requiere adaptación" }
        if restart_required == true { return "Pendiente de aplicar" }
        if !active { return "Desactivada" }
        return remote_allowed ? "Disponible en NVIDIA" : "Requiere autorización para NVIDIA"
    }
}

public struct JarvisExtensionPlugin: Decodable, Identifiable, Sendable {
    public let plugin_id: String
    public let name: String
    public let description: String
    public let version: String
    public let checksum_sha256: String?
    public let enabled: Bool
    public let active: Bool
    public let integrity: String
    public let skills: Int
    public let tools: Int
    public let restart_required: Bool
    public var id: String { plugin_id }
}

public struct JarvisSkillDetails: Decodable, Sendable {
    public let skill_id: String
    public let content_sha256: String
    public let instructions: [String]
    public let resources: [String]
}

public struct JarvisSkillImportPreview: Decodable, Sendable {
    public struct ResourceCandidate: Decodable, Identifiable, Sendable {
        public let path: String
        public let supported: Bool
        public let diagnostic: String?
        public var id: String { path }
    }
    public let source_sha256: String
    public let format: String
    public let skill_id: String
    public let name: String
    public let description: String
    public let instructions: [String]
    public let resources: [String]
    public let role: String?
    public let trigger_phrases: [String]
    public let trigger_terms: [String]
    public let allowed_tools: [String]
    public let starter_tools: [String]
    public let diagnostics: [String]
    public let requires_disabled: Bool
    public let resource_candidates: [ResourceCandidate]?
}

public struct JarvisPluginImportPreview: Decodable, Sendable {
    public struct Tool: Decodable, Identifiable, Sendable {
        public let name: String
        public let capability: String
        public let risk: String
        public let description: String
        public var id: String { name }
    }
    public struct Skill: Decodable, Identifiable, Sendable {
        public let skill_id: String
        public let name: String
        public let instructions: [String]
        public let resources: [String]
        public var id: String { skill_id }
    }
    public let source_sha256: String
    public let plugin_id: String
    public let name: String
    public let description: String
    public let version: String
    public let checksum_sha256: String
    public let skills: [Skill]
    public let tools: [Tool]
    public let hosts: [String]
    public let compatible: Bool
}
