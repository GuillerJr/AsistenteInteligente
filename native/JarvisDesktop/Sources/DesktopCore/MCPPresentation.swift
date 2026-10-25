import Foundation

public enum MCPPresentation {
    public static func errorMessage(_ error: Error) -> String {
        if let configuration = error as? MCPConfigurationError { return configuration.localizedDescription }
        guard let failure = error as? RPCFailure else { return "No se pudo completar la operación del servidor MCP." }
        guard case .remote(_, let message) = failure else { return failure.localizedDescription }
        switch message {
        case "mcp_server_id_invalid": return MCPConfigurationError.identifier.localizedDescription
        case "mcp_server_command_invalid": return MCPConfigurationError.command.localizedDescription
        case "mcp_protocol_version_invalid": return MCPConfigurationError.protocolVersion.localizedDescription
        case "mcp_server_not_found", "mcp_not_configured": return "El servidor ya no está configurado. Actualiza la lista."
        case "mcp_configuration_changed", "mcp_config_changed": return "La configuración cambió después de la revisión. Actualiza la lista y revisa el programa de nuevo."
        case "mcp_busy", "runs_active", "run_active", "active_runs": return "Espera a que terminen las ejecuciones antes de cambiar servidores MCP."
        case "mcp_server_limit": return "Se alcanzó el límite de servidores configurados. Quita uno antes de añadir otro."
        case "mcp_cleanup_incomplete":
            return "El cierre del programa y su grupo de procesos sigue sin confirmar. No se reconectó automáticamente. Vuelve a solicitar su cierre de forma explícita."
        default: return "El servidor MCP no completó la operación. No se repetirá automáticamente; actualiza su estado antes de volver a intentarlo."
        }
    }

    public static func serverError(_ code: String) -> String {
        errorMessage(RPCFailure.remote(-32020, code))
    }
}
