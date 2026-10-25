import Foundation

public enum TelegramPresentation {
    public static func errorMessage(_ error: Error) -> String {
        guard let failure = error as? RPCFailure else { return "No se pudo consultar o configurar Telegram." }
        guard case .remote(_, let code) = failure else { return failure.localizedDescription }
        switch code {
        case "telegram_token_invalid", "telegram_auth_failed", "telegram_login_required":
            return "Revisa el token del bot y conecta Telegram de nuevo. El token no se guarda."
        case "telegram_transport_failed":
            return "No se pudo completar la comunicación con Telegram. Revisa la conexión y actualiza el estado."
        case "telegram_connect_timeout":
            return "Se agotó la espera para verificar la identidad y conexión del bot. Actualiza el estado antes de intentarlo de nuevo."
        case "telegram_webhook_timeout":
            return "Se agotó la espera al comprobar si el bot tiene un webhook. No se confirmó su disponibilidad para este canal."
        case "telegram_webhook_conflict":
            return "El acceso al bot tiene un conflicto. Revisa si tiene un webhook activo o si otro programa está leyendo sus actualizaciones."
        case "telegram_rate_limited":
            return "Telegram limitó temporalmente las solicitudes. Espera antes de volver a activar el canal."
        case "telegram_api_rejected":
            return "Telegram rechazó la solicitud del bot. Revisa su acceso y configuración antes de continuar."
        case "telegram_response_invalid", "telegram_http_invalid", "telegram_http_encoding_unsupported",
             "telegram_http_limit", "telegram_response_limit", "telegram_updates_invalid":
            return "No se recibió una respuesta válida para este canal de Telegram. Actualiza el estado y revisa la conexión."
        case "telegram_client_closed":
            return "La conexión del bot está cerrada. Revisa su estado y conecta el bot para continuar."
        case "telegram_runner_failed":
            return "El canal se detuvo por un fallo interno que no quedó identificado. Revisa su estado antes de volver a activarlo."
        case "telegram_configuration_changed", "telegram_route_revision_conflict":
            return "Las rutas cambiaron desde la revisión. Actualiza y vuelve a revisarlas antes de continuar."
        case "telegram_stop_required", "telegram_runner_in_use":
            return "Detén el canal antes de modificar sus rutas o su conexión."
        case "telegram_disconnect_required":
            return "Desconecta el bot actual antes de conectar otro."
        case "telegram_route_limit": return "Se alcanzó el límite de 32 rutas. Revisa las existentes."
        case "telegram_identity_invalid": return "Introduce un identificador numérico positivo de chat privado."
        case "telegram_pairing_expired", "telegram_pairing_claim_invalid", "telegram_pairing_link_unavailable":
            return "El intento de enlace ya no está vigente. Actualiza el estado e inicia otro si lo necesitas."
        case "telegram_pairing_username_required":
            return "El bot debe tener un nombre público de Telegram para abrir su chat en la app."
        case "telegram_pairing_review_required":
            return "Revisa y confirma el consumo de actualizaciones y el uso exclusivo del bot antes de iniciar."
        case "telegram_pairing_active": return "Ya hay un intento de enlace activo. Confírmalo o cancélalo antes de iniciar otro."
        case "telegram_cleanup_incomplete":
            return "El cierre del lector de Telegram sigue sin confirmar. Solicita su cancelación explícita antes de continuar."
        case "telegram_route_unavailable", "telegram_workspace_unavailable":
            return "La conversación, el agente o la carpeta de esta ruta ya no están disponibles. Revisa las rutas."
        case "telegram_delivery_unconfirmed", "telegram_durability_uncertain", "telegram_dispatch_unconfirmed",
             "telegram_delivery_ack_invalid", "telegram_receipt_unavailable":
            return "La entrega o el guardado no están confirmados. Revisa los registros; no se repetirá la operación automáticamente."
        default:
            // Remote diagnostics can contain secrets. Never display an arbitrary
            // Telegram error body or response, including an echoed credential.
            return "No se confirmó la operación de Telegram. Actualiza el estado antes de continuar."
        }
    }
    public static func channelError(_ code: String) -> String {
        errorMessage(RPCFailure.remote(-32036, code))
    }
    public static func state(_ value: String) -> String {
        ["claimed": "reservado", "dispatching": "en ejecución", "send_pending": "envío pendiente",
         "completed": "completado", "ignored": "ignorado", "failed": "fallido", "interrupted": "interrumpido",
         "cancelled": "cancelado"][value] ?? "sin confirmar"
    }
}
