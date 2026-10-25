import Foundation

public enum ManagementPresentation {
    public static func errorMessage(_ error: Error) -> String {
        guard case .remote(-32035, let code) = error as? RPCFailure else {
            return CapabilityPresentation.errorMessage(error)
        }
        switch code {
        case "agent_revision_conflict", "skill_revision_conflict", "scheduler_revision_conflict", "registry_changed":
            return "La configuración cambió desde que la revisaste. Actualiza la lista y revisa de nuevo el cambio."
        case "skill_source_changed":
            return "La carpeta de la skill cambió desde la revisión. Vuelve a seleccionarla y revisa su contenido."
        case "agent_not_found", "skill_not_found", "scheduler_not_found":
            return "El elemento ya no está disponible. Actualiza la lista antes de continuar."
        case "agent_limit", "skill_limit", "skill_agent_limit", "scheduler_capacity_exceeded":
            return "Se alcanzó el límite de elementos. Revisa los existentes antes de añadir otro."
        case "agent_already_exists":
            return "Ese perfil ya existe. Actualiza la lista para revisarlo."
        case "skill_already_imported":
            return "Esta skill ya está importada para el agente. Revisa la existente."
        case "default_agent_required":
            return "El perfil Jarvis es necesario y no se puede eliminar."
        case "skill_frontmatter_required", "skill_frontmatter_invalid", "skill_frontmatter_unsupported",
             "skill_frontmatter_limit", "skill_resource_list_invalid", "skill_resource_path_invalid":
            return "El manifiesto de la skill no es compatible. Revisa su encabezado y la lista de recursos."
        case "skill_source_path_invalid", "skill_source_path_unsafe", "skill_source_unavailable",
             "skill_manifest_unavailable", "skill_manifest_unsafe", "skill_manifest_limit":
            return "No se pudo leer de forma segura el manifiesto de la carpeta elegida. Selecciona y revisa otra carpeta."
        case "scheduler_rule_invalid", "scheduler_time_invalid", "scheduler_timezone_invalid":
            return "La fecha, el intervalo o la zona horaria no son válidos. Revisa la programación."
        case "scheduler_finished_once", "scheduler_slot_already_claimed":
            return "Esa fecha ya tiene una ejecución registrada. Revisa su resultado antes de programar otra."
        case "scheduler_runner_in_use", "scheduler_busy", "registry_busy":
            return "Otro proceso está usando esta configuración. Espera a que termine antes de continuar."
        case "registry_durability_uncertain", "scheduler_dispatch_unconfirmed", "scheduler_cleanup_incomplete":
            return "No se confirmó el resultado del cambio o de la ejecución. Revisa el estado antes de continuar; no se repetirá automáticamente."
        case "agent_id_invalid", "agent_text_invalid", "agent_revision_invalid", "skill_agent_invalid",
             "skill_id_invalid", "skill_digest_invalid", "skill_revision_invalid", "skill_text_invalid",
             "skill_enabled_invalid", "scheduler_identity_invalid", "scheduler_scope_invalid",
             "scheduler_text_invalid", "scheduler_text_limit", "scheduler_revision_invalid":
            return "Los datos del cambio no son válidos o superan el límite permitido. Revisa el formulario."
        default:
            // Only this finite catalog is presented. Private registry paths,
            // unknown diagnostics and future machine codes remain internal.
            return "No se pudo completar el cambio de gestión. Actualiza el estado antes de continuar."
        }
    }

    public static func state(_ value: String) -> String {
        ["claimed": "reservada", "running": "en ejecución", "completed": "completada", "failed": "fallida",
         "cancelled": "cancelada", "interrupted": "interrumpida", "stopped": "detenido", "timed_out": "tiempo agotado",
         "output_limit": "límite de salida", "stdin_unconfirmed": "entrada sin confirmar", "scope_closed": "conversación cerrada",
         "manager_closed": "servicio cerrado"][value] ?? "sin confirmar"
    }
}
