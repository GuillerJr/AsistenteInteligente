import Foundation

public enum LocalVoiceTurnFeedback {
    public static func approvalExpiryDelay(
        expiresAt: Date,
        now: Date = Date()
    ) -> TimeInterval {
        min(max(expiresAt.timeIntervalSince(now) + 0.05, 0.05), 125)
    }

    public static func spokenPreflightFailure(
        microphoneAuthorized: Bool,
        speechRecognitionAuthorized: Bool,
        runtimeAvailable: Bool,
        screenCaptureAuthorized: Bool? = nil
    ) -> String {
        if !microphoneAuthorized {
            return "Necesito permiso para usar el micrófono."
        }
        if !speechRecognitionAuthorized {
            return "Necesito permiso para reconocer tu voz."
        }
        if screenCaptureAuthorized == false {
            return "Necesito permiso para ver la pantalla."
        }
        if !runtimeAvailable {
            return "Mi servicio local no está disponible. Inténtalo otra vez en un momento."
        }
        return "No pude iniciar el turno de voz."
    }

    public static func spokenCaptureFailure(for reason: String) -> String? {
        switch reason {
        case "cancelled":
            nil
        case "noAudibleInput", "noFinalTranscript":
            "No alcancé a escucharte. Di Jarvis e inténtalo otra vez."
        case "microphonePermission":
            "Necesito permiso para usar el micrófono."
        case "speechPermission":
            "Necesito permiso para reconocer tu voz."
        case "speakerIdentityUnverified":
            "No pude aislar tu voz del ruido o de otras personas. Acércate un poco y di Jarvis otra vez."
        case "unsupportedLocale", "recognizerUnavailable", "onDeviceRecognitionUnavailable":
            "El reconocimiento de voz local no está disponible."
        default:
            "No pude entenderte. Di Jarvis e inténtalo otra vez."
        }
    }

    public static func spokenJobFailure(for errorCode: String) -> String? {
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
        case "empty_agent_response", "job_result_invalid", "job_stream_invalid":
            "La respuesta llegó incompleta. Inténtalo otra vez."
        default:
            "No pude completar la solicitud."
        }
    }
}
