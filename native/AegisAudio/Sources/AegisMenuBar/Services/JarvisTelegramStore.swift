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
        case "connected": return String(localized: "Conectado", bundle: .module)
        case "connecting": return String(localized: "Conectando…", bundle: .module)
        case "awaiting_pair": return String(localized: "Abre el bot para vincular tu cuenta", bundle: .module)
        case "awaiting_confirmation": return String(localized: "Confirma tu cuenta en este Mac", bundle: .module)
        case "suspended": return String(localized: "Pausado por el estado del Mac", bundle: .module)
        default: return configured ? String(localized: "Desactivado", bundle: .module) : String(localized: "Sin configurar", bundle: .module)
        }
    }

    static func errorTitle(_ code: String) -> String {
        switch code {
        case "telegram_credential_invalid": String(localized: "El token no es válido. Revísalo en BotFather.", bundle: .module)
        case "telegram_webhook_configured": String(localized: "Este bot ya tiene otra integración. Usa un bot exclusivo para Jarvis.", bundle: .module)
        case "telegram_poll_conflict": String(localized: "Otra sesión está utilizando este bot. Detén esa sesión antes de conectar Jarvis.", bundle: .module)
        case "telegram_rate_limited": String(localized: "Telegram pidió una pausa. Jarvis volverá a intentarlo.", bundle: .module)
        case "telegram_invalid_configuration": String(localized: "Revisa el token o vuelve a vincular la cuenta.", bundle: .module)
        case "telegram_credential_unavailable": String(localized: "No se pudo acceder al token en el llavero.", bundle: .module)
        case "telegram_journal_invalid": String(localized: "La recuperación de Telegram está dañada. El canal está bloqueado; el resto de Jarvis sigue disponible.", bundle: .module)
        case "telegram_inbox_full": String(localized: "La cola de Telegram está llena. Jarvis retomará la recepción al procesarla.", bundle: .module)
        case "telegram_delivery_failed": String(localized: "No se pudo entregar la respuesta. Envía otra vez tu instrucción.", bundle: .module)
        default: String(localized: "No se pudo conectar. Comprueba Internet y el servicio de Jarvis.", bundle: .module)
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
        catch { if snapshot == nil { failed = true; message = String(localized: "No se pudo leer Telegram. Comprueba el servicio de Jarvis.", bundle: .module) } }
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
                message = String(localized: "Bot verificado. El token está guardado en el llavero. Ahora vincula tu móvil.", bundle: .module)
            } else if method == "telegram.journal.reset" {
                message = String(localized: "Recuperación restablecida. Activa Telegram para reconectar; el token, tu cuenta y los chats se conservaron.", bundle: .module)
            }
        }
        catch let error as TelegramSettingsError {
            failed = true
            message = JarvisTelegramSnapshot.errorTitle(error.code)
        }
        catch let error as LocalIPCError {
            failed = true
            message = error == .invalidConfiguration
                ? String(localized: "La app rechazó la configuración de la conexión. Comprueba que Jarvis esté actualizado.", bundle: .module)
                : String(localized: "No se pudo comunicar con el servicio. Actualiza el estado antes de volver a intentarlo.", bundle: .module)
        }
        catch { failed = true; message = String(localized: "No se completó el cambio. Actualiza el estado e inténtalo nuevamente.", bundle: .module) }
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
