import Foundation

public enum RunPresentation {
    public static func errorMessage(_ code: String) -> String {
        switch code {
        case "codex_login_required": return "Inicia sesión con tu cuenta de ChatGPT para continuar."
        case "codex_thread_recovery_required", "thread_recovery_required":
            return "Codex no pudo recuperar el contexto. La ejecución no se repetirá; inicia otra conversación."
        case "codex_thread_busy": return "Codex sigue trabajando en esta conversación. Espera a que termine."
        case "codex_request_timeout": return "Codex tardó demasiado en responder. Consulta el estado antes de volver a enviar."
        case "codex_answer_too_large", "codex_frame_too_large":
            return "La respuesta superó el límite permitido. No se guardó como una respuesta completada."
        case "codex_disconnected", "codex_closed":
            return "Se interrumpió la conexión con Codex. La consulta no se repetirá automáticamente."
        case "codex_turn_failed", "turn_failed": return "Codex no pudo completar esta consulta."
        default: return "La ejecución no pudo completarse. Consulta su estado antes de volver a enviar."
        }
    }

    public static func appendDelta(_ value: String, to current: String) throws -> String {
        guard current.utf8.count + value.utf8.count <= 32_000 else { throw RPCFailure.oversized }
        return current + value
    }
}
