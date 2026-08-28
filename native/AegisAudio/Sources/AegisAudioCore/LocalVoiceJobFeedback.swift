import Foundation

public enum LocalVoiceJobFeedback {
    public static func approvalExpiryDelay(
        expiresAt: Date,
        now: Date = Date()
    ) -> TimeInterval {
        min(max(expiresAt.timeIntervalSince(now) + 0.05, 0.05), 125)
    }

    public static func spokenFailure(for errorCode: String) -> String? {
        switch errorCode {
        case "job_superseded", "job_cancelled":
            nil
        case "confirmation_expired", "confirmation_consumption_failed":
            "La aprobación caducó. No ejecuté la acción."
        case "approved_tool_execution_failed":
            "No pude completar la acción aprobada."
        case "multiple_confirmations_unsupported":
            "Esa solicitud requiere varias aprobaciones. Pídeme las acciones una por una."
        case "secret_material_rejected":
            "No procesé la solicitud porque parecía contener una credencial."
        case "owner_verification_required", "voice_conversation_owner_required":
            "No pude verificar que fueras tú. No ejecuté la solicitud."
        case "conversation_capacity_reached", "conversation_not_found",
             "conversation_unavailable":
            "No pude continuar esta conversación. Podemos empezar un turno nuevo."
        case "job_timeout", "swarm_execution_timeout":
            "La respuesta tardó demasiado. Inténtalo otra vez."
        default:
            "No pude completar la solicitud."
        }
    }
}
