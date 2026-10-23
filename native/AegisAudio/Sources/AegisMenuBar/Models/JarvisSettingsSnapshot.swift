import Foundation

struct JarvisSelectableModel: Codable, Equatable, Identifiable, Sendable {
    var model_id: String
    var images = false
    var tools = false
    var id: String { model_id }
}

struct JarvisRoleSelection: Codable, Equatable, Sendable {
    var primary: String
    var fallback: String?
}

struct JarvisModelPreferences: Codable, Equatable, Sendable {
    var version = 1
    var models: [JarvisSelectableModel]
    var roles: [String: JarvisRoleSelection]
}

struct JarvisIntegrationSettings: Decodable, Identifiable, Sendable {
    let server_id: String
    let enabled: Bool
    let provider: String
    let accounts: [String]
    let approved_tools: Int
    let state: String
    let available_tools: Int
    let google_configured: Bool
    var browser_mode: String?
    var browser_origins: [String]?
    var browser_remote_content_allowed: Bool?
    var id: String { server_id }
    var title: String {
        switch provider {
        case "google-workspace": String(localized: "Google Workspace", bundle: .module)
        case "github": "GitHub"
        case "filesystem": String(localized: "Archivos locales", bundle: .module)
        case "playwright": String(localized: "Navegador", bundle: .module)
        case "home-assistant": String(localized: "Home Assistant", bundle: .module)
        default: server_id
        }
    }
    var statusTitle: String {
        switch state {
        case "active": String(localized: "Conectado · \(available_tools) herramientas", bundle: .module)
        case "quarantined", "blocked": String(localized: "Requiere revisión", bundle: .module)
        case "disabled": enabled ? String(localized: "Pendiente de aplicar", bundle: .module) : String(localized: "Desactivado", bundle: .module)
        default: String(localized: "Sin conexión", bundle: .module)
        }
    }
}

struct JarvisSettingsSnapshot: Decodable, Sendable {
    let models: JarvisModelPreferences
    let models_valid: Bool
    let credential: String
    let servers: [JarvisIntegrationSettings]
    let restart_required: Bool
    let memory_namespace: String
    var browser_automation: JarvisBrowserAutomationSettings?
}

struct JarvisBrowserAutomationSettings: Decodable, Sendable {
    let enabled: Bool
    let mode: String
    let connection: String

    var modeTitle: String {
        switch mode {
        case "chrome": String(localized: "Chrome directo", bundle: .module)
        case "isolated": String(localized: "Navegador separado", bundle: .module)
        default: String(localized: "Con extensión", bundle: .module)
        }
    }
}

struct JarvisSettingsOperation: Decodable, Sendable {
    let state: String
    var reason: String?
    var url: String?
    var restart_required: Bool?
}

struct JarvisMemoryHit: Decodable, Identifiable, Sendable {
    let memory_id: String
    let excerpt: String
    var id: String { memory_id }
}

/// A read-only tool the owner chose to always allow ("Aprobar siempre").
struct JarvisStandingGrant: Decodable, Sendable, Identifiable, Equatable {
    let tool_name: String
    let granted_at: String
    var id: String { tool_name }

    /// "mcp_google_workspace__search_gmail_messages" → "google workspace · search gmail messages"
    var label: String {
        var name = tool_name
        if name.hasPrefix("mcp_") { name.removeFirst(4) }
        return name.replacingOccurrences(of: "__", with: " · ")
            .replacingOccurrences(of: "_", with: " ")
    }
}

struct JarvisScheduledTask: Decodable, Sendable, Identifiable, Equatable {
    let task_id: String
    let title: String
    let prompt: String
    let schedule: String
    let notify: String
    let enabled: Bool
    let next_run_at: String?
    let last_status: String?
    // Read by the scheduled tasks page; optional so older daemons still decode.
    var reminder: Bool? = nil
    var last_run_at: String? = nil
    var last_result: String? = nil
    var conversation_id: String? = nil
    var spec: Spec? = nil
    var id: String { task_id }

    struct Spec: Decodable, Sendable, Equatable {
        let kind: String
        let time: String?
        let weekdays: [String]
        let every_minutes: Int?
        let at: String?
    }
}

/// A multi-step goal Jarvis works on alone (Codex's goals).
struct JarvisGoal: Decodable, Sendable, Identifiable, Equatable {
    let goal_id: String
    let title: String
    let objective: String
    let status: String
    let status_label: String
    let steps_used: Int
    let max_steps: Int
    let notes: [String]
    var id: String { goal_id }
    var active: Bool { status == "active" }
}

struct JarvisGoals: Decodable, Sendable {
    let goals: [JarvisGoal]
}

struct JarvisPersonality: Decodable, Sendable, Equatable {
    struct Preset: Decodable, Sendable, Equatable, Hashable { let id: String; let label: String }
    let preset: String
    let custom: String
    let presets: [Preset]
}

struct JarvisScheduledTasks: Decodable, Sendable {
    let tasks: [JarvisScheduledTask]
}

struct JarvisAppGrant: Decodable, Sendable, Identifiable, Equatable {
    let bundle_identifier: String
    let granted_at: String
    var id: String { bundle_identifier }
}

struct JarvisAppGrants: Decodable, Sendable {
    let grants: [JarvisAppGrant]
}

struct JarvisStandingGrants: Decodable, Sendable {
    let grants: [JarvisStandingGrant]
}

struct JarvisMemoryResults: Decodable, Sendable {
    let hits: [JarvisMemoryHit]
}

enum JarvisSettingsSection: String, CaseIterable, Identifiable {
    case general, accounts, telegram, models, extensions, voice, privacy, devices, diagnostics
    var id: String { rawValue }
    var title: String {
        switch self {
        case .general: String(localized: "General", bundle: .module)
        case .accounts: String(localized: "Cuentas e integraciones", bundle: .module)
        case .telegram: String(localized: "Telegram", bundle: .module)
        case .models: String(localized: "Modelos de IA", bundle: .module)
        case .extensions: String(localized: "Extensiones", bundle: .module)
        case .voice: String(localized: "Voz", bundle: .module)
        case .privacy: String(localized: "Memoria y privacidad", bundle: .module)
        case .devices: String(localized: "Dispositivos", bundle: .module)
        case .diagnostics: String(localized: "Diagnóstico", bundle: .module)
        }
    }
    var symbol: String {
        switch self {
        case .general: "slider.horizontal.3"
        case .accounts: "person.crop.circle"
        case .telegram: "paperplane"
        case .models: "cpu"
        case .extensions: "puzzlepiece.extension"
        case .voice: "waveform"
        case .privacy: "lock.shield"
        case .devices: "house"
        case .diagnostics: "stethoscope"
        }
    }
    var detail: String {
        switch self {
        case .general: String(localized: "Preferencias de Jarvis en este Mac.", bundle: .module)
        case .accounts: String(localized: "Administra las conexiones y la cuenta que utiliza Jarvis.", bundle: .module)
        case .telegram: String(localized: "Conversa con Jarvis desde tu móvil.", bundle: .module)
        case .models: String(localized: "Conecta tu cuenta de ChatGPT y elige el modelo de Codex para Jarvis.", bundle: .module)
        case .extensions: String(localized: "Revisa y administra las skills y los plugins que utiliza Jarvis.", bundle: .module)
        case .voice: String(localized: "Identidad y activación local con la voz actual de Jarvis.", bundle: .module)
        case .privacy: String(localized: "Revisa los permisos y consulta los recuerdos guardados.", bundle: .module)
        case .devices: String(localized: "Conexiones para tus dispositivos y tu hogar.", bundle: .module)
        case .diagnostics: String(localized: "Estado del servicio y de sus componentes.", bundle: .module)
        }
    }
}

enum JarvisModelTask {
    static let roles = ["router", "planner", "critical_reasoner", "code_security",
                        "vision", "omni", "synthesizer"]
    static func title(_ role: String) -> String {
        switch role {
        case "router": String(localized: "Selección de tareas", bundle: .module)
        case "planner": String(localized: "Planificación y herramientas", bundle: .module)
        case "critical_reasoner": String(localized: "Razonamiento complejo", bundle: .module)
        case "code_security": String(localized: "Código y revisión", bundle: .module)
        case "vision": String(localized: "Análisis de imágenes", bundle: .module)
        case "omni": String(localized: "Análisis multimodal", bundle: .module)
        default: String(localized: "Redacción de respuestas", bundle: .module)
        }
    }
    static func compatible(_ model: JarvisSelectableModel, role: String) -> Bool {
        switch role {
        case "vision", "omni": model.images
        case "synthesizer": true
        default: model.tools
        }
    }
}
