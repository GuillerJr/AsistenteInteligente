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

struct JarvisMemoryResults: Decodable, Sendable {
    let hits: [JarvisMemoryHit]
}

enum JarvisSettingsSection: String, CaseIterable, Identifiable {
    case general, accounts, telegram, models, voice, privacy, devices, diagnostics
    var id: String { rawValue }
    var title: String {
        switch self {
        case .general: "General"
        case .accounts: "Cuentas e integraciones"
        case .telegram: "Telegram"
        case .models: "Modelos de IA"
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
