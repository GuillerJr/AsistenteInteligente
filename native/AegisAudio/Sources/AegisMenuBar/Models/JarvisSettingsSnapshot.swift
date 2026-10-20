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
        case "google-workspace": "Google Workspace"
        case "github": "GitHub"
        case "filesystem": "Archivos locales"
        case "playwright": "Navegador"
        case "home-assistant": "Home Assistant"
        default: server_id
        }
    }
    var statusTitle: String {
        switch state {
        case "active": "Conectado · \(available_tools) herramientas"
        case "quarantined", "blocked": "Requiere revisión"
        case "disabled": enabled ? "Pendiente de aplicar" : "Desactivado"
        default: "Sin conexión"
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
        case "chrome": "Chrome directo"
        case "isolated": "Navegador separado"
        default: "Con extensión"
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
    var id: String { task_id }
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
        case .general: "General"
        case .accounts: "Cuentas e integraciones"
        case .telegram: "Telegram"
        case .models: "Modelos de IA"
        case .extensions: "Extensiones"
        case .voice: "Voz"
        case .privacy: "Memoria y privacidad"
        case .devices: "Dispositivos"
        case .diagnostics: "Diagnóstico"
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
        case .general: "Preferencias de Jarvis en este Mac."
        case .accounts: "Administra las conexiones y la cuenta que utiliza Jarvis."
        case .telegram: "Conversa con Jarvis desde tu móvil."
        case .models: "Elige un modelo principal y una alternativa para cada tarea."
        case .extensions: "Revisa y administra las skills y los plugins que utiliza Jarvis."
        case .voice: "Identidad y activación local con la voz actual de Jarvis."
        case .privacy: "Revisa los permisos y consulta los recuerdos guardados."
        case .devices: "Conexiones para tus dispositivos y tu hogar."
        case .diagnostics: "Estado del servicio y de sus componentes."
        }
    }
}

enum JarvisModelTask {
    static let roles = ["router", "planner", "critical_reasoner", "code_security",
                        "vision", "omni", "synthesizer"]
    static func title(_ role: String) -> String {
        switch role {
        case "router": "Selección de tareas"
        case "planner": "Planificación y herramientas"
        case "critical_reasoner": "Razonamiento complejo"
        case "code_security": "Código y revisión"
        case "vision": "Análisis de imágenes"
        case "omni": "Análisis multimodal"
        default: "Redacción de respuestas"
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
