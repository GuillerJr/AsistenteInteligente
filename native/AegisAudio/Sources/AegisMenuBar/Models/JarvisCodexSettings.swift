import Foundation

struct JarvisCodexModel: Decodable, Equatable, Identifiable, Sendable {
    let id: String
    let display_name: String
    let images: Bool
    let is_default: Bool
}

struct JarvisCodexTest: Decodable, Equatable, Sendable {
    enum State: String, Decodable { case idle, running, passed, failed, cancelled }
    let state: State
    let error_code: String?
    let latency_ms: Int?
}

struct JarvisCodexStatus: Decodable, Equatable, Sendable {
    enum State: String, Decodable { case ready, signed_out, login_pending, missing, unavailable }
    let provider: String
    let state: State
    let installed: Bool
    let model_id: String?
    let models: [JarvisCodexModel]
    let last_test: JarvisCodexTest?

    var ready: Bool { state == .ready && installed }
    var compatibleModels: [JarvisCodexModel] { models.filter(\.images) }
    var hasCompatibleSelection: Bool {
        compatibleModels.contains { $0.id == model_id }
    }
    var title: String {
        switch state {
        case .ready: String(localized: "ChatGPT conectado", bundle: .module)
        case .signed_out: String(localized: "Inicia sesión con ChatGPT", bundle: .module)
        case .login_pending: String(localized: "Completa el inicio de sesión en el navegador", bundle: .module)
        case .missing: String(localized: "Codex no está disponible en este Mac", bundle: .module)
        case .unavailable: String(localized: "No se pudo verificar la conexión con Codex", bundle: .module)
        }
    }

    func validate() throws {
        guard provider == "codex", models.count <= 128,
              Set(models.map(\.id)).count == models.count,
              model_id.map(Self.safeIdentifier) ?? true,
              models.allSatisfy({ Self.safeIdentifier($0.id)
                  && !$0.display_name.isEmpty && $0.display_name.count <= 256
                  && !$0.display_name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) }),
              last_test?.latency_ms.map({ (0...3_600_000).contains($0) }) ?? true,
              state != .ready || installed else { throw JarvisCodexSettingsError.invalidResponse }
    }

    static func safeIdentifier(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 256
            && value.unicodeScalars.allSatisfy { $0.isASCII && !CharacterSet.whitespacesAndNewlines.contains($0)
                && !CharacterSet.controlCharacters.contains($0) }
    }
}

enum JarvisCodexSettingsError: Error { case invalidResponse, request(String) }

enum JarvisCodexLoginURL {
    static func validated(_ text: String) -> URL? {
        guard text.utf8.count <= 8_192,
              let components = URLComponents(string: text),
              components.scheme == "https",
              let host = components.host,
              ["auth.openai.com", "chatgpt.com"].contains(host),
              components.user == nil, components.password == nil,
              components.port == nil || components.port == 443,
              components.fragment == nil,
              !text.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
        else { return nil }
        return components.url
    }
}
