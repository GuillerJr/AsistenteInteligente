import AegisAudioCore
import Foundation
import Observation

struct JarvisTelegramCandidate: Decodable, Sendable {
    let id: Int64
    let name: String
}

struct JarvisTelegramSnapshot: Decodable, Sendable {
    let state: String
    let enabled: Bool
    let configured: Bool
    let bot_username: String?
    let owner_id: Int64?
    let owner_name: String?
    let candidate: JarvisTelegramCandidate?
    let error: String?
    let pair_url: String?

    var statusTitle: String {
        if let error { return Self.errorTitle(error) }
        switch state {
        case "connected": return "Conectado"
        case "connecting": return "Conectando…"
        case "awaiting_pair": return "Abre el bot para vincular tu cuenta"
        case "awaiting_confirmation": return "Confirma tu cuenta en este Mac"
        case "suspended": return "Pausado por el estado del Mac"
        default: return configured ? "Desactivado" : "Sin configurar"
        }
    }

    static func errorTitle(_ code: String) -> String {
        switch code {
        case "telegram_credential_invalid": "El token no es válido. Revísalo en BotFather."
        case "telegram_webhook_configured": "Este bot ya tiene otra integración. Usa un bot exclusivo para Jarvis."
        case "telegram_poll_conflict": "Otra sesión está utilizando este bot. Detén esa sesión antes de conectar Jarvis."
        case "telegram_rate_limited": "Telegram pidió una pausa. Jarvis volverá a intentarlo."
        case "telegram_invalid_configuration": "Revisa el token o vuelve a vincular la cuenta."
        case "telegram_credential_unavailable": "No se pudo acceder al token en el llavero."
        case "telegram_delivery_failed": "No se pudo entregar la respuesta. Envía otra vez tu instrucción."
        default: "No se pudo conectar. Comprueba Internet y el servicio de Jarvis."
        }
    }
}

private struct TelegramSettingsError: Error { let code: String }

@MainActor @Observable
final class JarvisTelegramStore {
    var snapshot: JarvisTelegramSnapshot?
    var busy = false
    var message: String?
    var failed = false

    func refresh() async {
        guard !busy else { return }
        do { snapshot = try await call("telegram.status") }
        catch { if snapshot == nil { failed = true; message = "No se pudo leer Telegram. Comprueba el servicio de Jarvis." } }
    }

    func perform(_ method: String, payload: [String: Any] = [:]) async {
        guard !busy else { return }
        busy = true
        message = nil
        failed = false
        defer { busy = false }
        do {
            snapshot = try await call(method, payload: payload)
            if method == "telegram.configure" {
                message = "Bot verificado. El token está guardado en el llavero. Ahora vincula tu móvil."
            }
        }
        catch let error as TelegramSettingsError {
            failed = true
            message = JarvisTelegramSnapshot.errorTitle(error.code)
        }
        catch let error as LocalIPCError {
            failed = true
            message = error == .invalidConfiguration
                ? "La app rechazó la configuración de la conexión. Comprueba que Jarvis esté actualizado."
                : "No se pudo comunicar con el servicio. Actualiza el estado antes de volver a intentarlo."
        }
        catch { failed = true; message = "No se completó el cambio. Actualiza el estado e inténtalo nuevamente." }
    }

    private func call(_ method: String, payload: [String: Any] = [:]) async throws -> JarvisTelegramSnapshot {
        let input = try JSONSerialization.data(withJSONObject: payload)
        let data = try await Task.detached(priority: .userInitiated) {
            let decoded = try JSONSerialization.jsonObject(with: input) as? [String: Any] ?? [:]
            let client = try LocalIPCClient(secretStore: MacOSIPCSecretStore(), timeoutSeconds: 10)
            let response: LocalIPCResponse
            if method == "telegram.configure" {
                guard let token = decoded["token"] as? String, decoded["confirm"] as? Bool == true else {
                    throw LocalIPCError.invalidConfiguration
                }
                response = try client.configureTelegram(token: token)
            } else {
                response = try client.call(method: method, payload: decoded)
            }
            guard response.ok else { throw TelegramSettingsError(code: response.errorCode ?? "telegram_unavailable") }
            return try JSONSerialization.data(withJSONObject: response.payload)
        }.value
        return try JSONDecoder().decode(JarvisTelegramSnapshot.self, from: data)
    }
}
