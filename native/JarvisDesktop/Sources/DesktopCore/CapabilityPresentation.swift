import Foundation

public enum CapabilityPresentation {
    public static func errorMessage(_ error: Error) -> String {
        guard let failure = error as? RPCFailure else { return "No se pudo completar la operación." }
        guard case .remote(_, let message) = failure else { return failure.localizedDescription }
        switch message {
        case "stale_citation":
            return "La fuente cambió o se eliminó. Busca de nuevo para obtener una cita vigente."
        case "workspace_required", "workspace_not_selected", "no_workspace_selected":
            return "Elige una carpeta de trabajo para continuar."
        case "approval_expired", "approval_not_found", "approval_stale", "approval_digest_mismatch":
            return "La solicitud ya no está vigente o cambió. No se autorizó una operación nueva."
        case "session_archived":
            return "Restaura la conversación archivada antes de enviar otro mensaje."
        case "session_not_found":
            return "La conversación ya no está disponible en este servicio."
        case "run_not_found":
            return "El servicio todavía no confirma la ejecución solicitada. No se volverá a enviar automáticamente."
        default:
            let machineCode = !message.isEmpty && message.utf8.count <= 128 && message.utf8.allSatisfy {
                (97...122).contains($0) || (48...57).contains($0) || $0 == 95
            }
            return machineCode ? "No se pudo completar la operación en el ámbito elegido." : message
        }
    }
}
