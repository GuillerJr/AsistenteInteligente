import Foundation

public enum RecoveryExtensionPresentation {
    public static func code(_ value: String) -> String {
        switch value {
        case "profile_repair_closed_source_required": return "Esta reparación necesita la base cerrada. Cierra el servicio y abre un perfil de prueba con esa base fría; no se reparará una conexión activa."
        case "profile_cold_repair_not_needed": return "No se encontró un daño compatible que necesite esta reparación. El original se conserva."
        case "profile_cold_repair_kind_unsupported", "profile_repair_kind_invalid": return "Esta reparación no es compatible con la base seleccionada."
        case "workspace_recovery_workspace_required": return "Selecciona primero una carpeta de trabajo disponible."
        case "workspace_recovery_private_path", "workspace_recovery_source_forbidden": return "La selección incluye un nombre o raíz privada protegida. No se copiarán credenciales ni estado privado."
        case "workspace_recovery_source_changed", "workspace_recovery_archive_review_changed": return "El origen cambió después de revisarlo. Descarta esta revisión y prepara otra; no se repetirá el efecto."
        case "workspace_recovery_destination_exists", "recovery_destination_exists": return "El destino ya existe. Elige un nombre nuevo; no se sobrescribirá."
        case "release_recovery_copy_stale", "release_recovery_copy_source_mismatch": return "El respaldo verificado ya no coincide con el perfil actual. Prepara y verifica una copia nueva en Mantenimiento."
        case "release_pending_requires_reconciliation": return "La instalación conserva una publicación sin resolver. Inspecciona su estado; no repitas la actualización ni el rollback."
        case "release_no_current_version": return "Todavía no hay una versión local seleccionada para consultar su arranque o rollback."
        case "recovery_cancelled", "recovery_deadline_exceeded": return "La operación se canceló o agotó su tiempo. Consulta el recibo y completa cualquier limpieza pendiente."
        default: return MaintenancePresentation.operationError(value) ?? "La recuperación no se completó. Revisa el diagnóstico y el estado antes de preparar otra operación."
        }
    }
    public static func error(_ error: Error) -> String {
        if case let RPCFailure.remote(_, message) = error,
           message.range(of: "^[a-z][a-z0-9_]{0,127}\\z", options: .regularExpression) != nil { return code(message) }
        return MaintenancePresentation.error(error)
    }
}
