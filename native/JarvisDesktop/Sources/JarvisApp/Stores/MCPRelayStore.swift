import DesktopCore
import Foundation

@MainActor extension MCPStore {
    func refreshRelay() async {
        guard runtimeIsActive(), let client, !isChanging else { return }
        let version = generation
        do { let result = try await client.relayStatus(); if version == generation { relay = result } }
        catch { if version == generation { report(error) } }
    }
    func changeRelay(_ action: String, exports: [String] = []) async {
        guard runtimeIsActive(), !isChanging, let client, let scope = context else { return }
        let version = generation
        isChanging = true
        defer { isChanging = false }
        do {
            let review = try await client.reviewRelay(scope: scope, action: action, exports: exports.sorted())
            let allow = OwnerManagementReview.confirm(title: "Revisar servidor MCP de Jarvis", action: "Autorizar esta operación",
                detail: "Se comparte únicamente el catálogo elegido con un cliente local autenticado. El bearer no elimina las aprobaciones: cada llamada crea una operación vinculada a la conversación revisada. No se exportan cuentas ni credenciales de Codex.\n\n"
                    + ManagementValidation.json(review.value),
                isCurrent: { self.runtimeIsActive() && self.generation == version && self.context == scope && review.expiresAt > Date() })
            let result = try await client.decideRelay(review, allow: allow)
            guard version == generation else { return }
            if allow { relay = try MCPRelaySnapshot(result["relay"]) }
            else if result["denied"].bool != true { throw RPCFailure.invalidResponse }
        } catch { if version == generation { report(error) } }
    }
    func ownerRelayToken() async -> String? {
        guard runtimeIsActive(), !isChanging, let client, relay?.running == true,
              OwnerManagementReview.confirm(title: "Acceso del cliente MCP local", action: "Mostrar token en campo protegido",
                detail: "El token permite conectarse al relay local. No lo pegues en el chat ni lo entregues a una App MCP. Cada herramienta conserva su aprobación por operación.", isCurrent: runtimeIsActive) else { return nil }
        let version = generation, snapshot = relay
        isChanging = true
        defer { isChanging = false }
        do {
            let token = try await client.relayToken()
            guard version == generation, relay == snapshot, runtimeIsActive() else { return nil }
            return token
        }
        catch { report(error); return nil }
    }
}
