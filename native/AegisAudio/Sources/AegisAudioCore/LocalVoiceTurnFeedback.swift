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
        case "cosmos_result_unknown":
            "No pude confirmar el resultado de Cosmos. Podría seguir procesándose en NVIDIA; no lo repetí."
        case "cosmos_authentication_failed":
            "NVIDIA no autorizó Cosmos con tu clave. Revisa la conexión en Configuración."
        case "cosmos_model_unavailable":
            "El endpoint de Cosmos no está disponible o tu clave no tiene acceso."
        case "cosmos_rate_limited":
            "NVIDIA limitó la generación. Inténtalo más tarde."
        case "cosmos_busy":
            "Cosmos ya está generando otro archivo. Espera a que termine."
        case "cosmos_request_rejected":
            "NVIDIA rechazó la generación. Revisa el prompt y sus parámetros."
        case "cosmos_response_invalid", "generated_media_invalid":
            "Cosmos no devolvió un archivo válido. No publiqué ese resultado."
        case "generated_media_capacity":
            "El almacenamiento de archivos generados alcanzó su límite."
        case "cosmos_unavailable", "generated_media_store_invalid":
            "No pude completar la generación con Cosmos."
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
        case "brain_unavailable":
            "El servicio de inteligencia no está disponible. Revisa la configuración de NVIDIA."
        case "remote_provider_unavailable":
            "NVIDIA no pudo responder. Inténtalo de nuevo."
        case "remote_rate_limited":
            "NVIDIA limitó temporalmente las consultas. Espera unos segundos y vuelve a intentarlo."
        case "remote_authentication_failed":
            "NVIDIA rechazó la credencial. Revisa la conexión en Configuración."
        case "tcc_permission_denied":
            "macOS retiró un permiso necesario. Detuve la acción sin reintentar."
        case "security_compromised":
            "La verificación de seguridad falló. Bloqueé la ejecución."
        case "job_status_unavailable":
            "No pude verificar el estado final de la tarea."
        case "empty_agent_response", "job_result_invalid", "job_stream_invalid":
            "La respuesta llegó incompleta. Inténtalo otra vez."
        default:
            "No pude completar la solicitud."
        }
    }

    public static func shortJobFailureTitle(for errorCode: String?) -> String {
        switch errorCode {
        case "speech_output_unavailable":
            "Voz no disponible"
        case "speech_output_interrupted":
            "Audio interrumpido"
        case "speech_replay_unavailable":
            "Audio reciente no disponible"
        case "speech_text_invalid":
            "Respuesta fuera del límite de voz"
        case "job_status_unavailable":
            "Resultado sin confirmar"
        case "confirmation_expired", "confirmation_consumption_failed":
            "Aprobación no válida"
        case "noAudibleInput", "noFinalTranscript":
            "No pude escucharte"
        case "microphonePermission", "speechPermission":
            "Permiso de voz pendiente"
        case "brain_unavailable":
            "Inteligencia no disponible"
        case "remote_provider_unavailable":
            "NVIDIA no disponible"
        case "remote_rate_limited":
            "Límite temporal de NVIDIA"
        case "remote_authentication_failed":
            "Credencial de NVIDIA no válida"
        case "job_timeout", "swarm_execution_timeout":
            "Tiempo de respuesta agotado"
        case "tcc_permission_denied":
            "Permiso de macOS retirado"
        case "security_compromised":
            "Ejecución bloqueada por seguridad"
        case "empty_agent_response", "job_result_invalid", "job_stream_invalid":
            "Respuesta incompleta"
        case "conversation_capacity_reached", "conversation_not_found",
             "conversation_unavailable":
            "Conversación no disponible"
        default:
            "Solicitud no completada"
        }
    }
}
