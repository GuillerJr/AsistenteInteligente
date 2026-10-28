import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable final class PluginStore {
    var plugins: [PluginDescriptor] = []
    var selectedAgentID = "jarvis"
    var isChanging = false
    var isLoading = false
    var errorMessage: String?
    var versions: [String: [ExtensionVersion]] = [:]
    var catalogs: [String: RPCValue] = [:]
    var resource: ExtensionResource?
    var lastReceipt: RPCValue?
    var cleanupReceipt: RPCValue?
    @ObservationIgnored private var client: PluginManagementClient?
    @ObservationIgnored private var runtimeIsActive: @Sendable () -> Bool = { false }
    @ObservationIgnored private var generation = 0
    @ObservationIgnored var onRunStarted: (@MainActor (String, SessionScope) -> Void)?

    func configure(_ transport: LocalRPCClient) {
        generation += 1; client = PluginManagementClient(transport: transport); runtimeIsActive = { transport.isActive }
    }
    func close() async { generation += 1; await client?.close() }
    func selectAgent(_ agent: String) {
        guard !isChanging, agent != selectedAgentID else { return }
        generation += 1; selectedAgentID = agent; plugins = []; versions = [:]; catalogs = [:]; resource = nil
    }
    func refresh() async {
        guard runtimeIsActive(), !isChanging, !isLoading, let client else { return }
        let agent = selectedAgentID, version = generation
        isLoading = true
        defer { isLoading = false }
        do {
            let rows = try await client.list(agent: agent)
            guard version == generation, agent == selectedAgentID else { return }
            plugins = rows; errorMessage = nil
        } catch { if version == generation { report(error) } }
    }
    func inspect(path: String) async -> PluginPackagePreview? {
        guard runtimeIsActive(), !isChanging, let client else { return nil }
        let version = generation
        do { let preview = try await client.inspect(path: path); return version == generation ? preview : nil }
        catch { if version == generation { report(error) }; return nil }
    }
    func change(_ action: String, plugin: PluginDescriptor? = nil, path: String? = nil,
                package: PluginPackagePreview? = nil, target: String? = nil) async -> Bool {
        guard runtimeIsActive(), !isChanging, let client,
              plugin == nil || plugins.contains(plugin!) else { return false }
        let agent = selectedAgentID, version = generation
        isChanging = true; errorMessage = nil
        defer { isChanging = false }
        var fields = plugin?.revisionParams ?? [:]
        fields.removeValue(forKey: "agent_id")
        if let path { fields["path"] = .string(path) }
        if let package { fields["package_sha256"] = .string(package.hash) }
        if let target { fields["target_version"] = .string(target) }
        var succeeded = false
        do {
            let review = try await client.review(action: action, agent: agent, fields: fields)
            let stillCurrent = { self.runtimeIsActive() && version == self.generation && agent == self.selectedAgentID
                && review.expiresAt > Date() && (plugin == nil || self.plugins.contains(plugin!)) }
            guard stillCurrent() else { return false }
            let allow = OwnerManagementReview.confirm(title: "Revisar \(Self.actionTitle(action))", action: "Autorizar esta operación",
                detail: "Los archivos y declaraciones del paquete son contenido externo sin verificación. Instalar o actualizar conserva la extensión deshabilitada. Los descriptores MCP no conectan programas automáticamente; cada efecto de una herramienta requiere su autorización.\n\n"
                    + ManagementValidation.json(review.value), isCurrent: stillCurrent)
            let result = try await client.decide(review, allow: allow)
            guard version == generation, agent == selectedAgentID else { return false }
            if let receipt = result.object?["receipt"] {
                guard receipt.object != nil, try JSONEncoder().encode(receipt).count <= 65536 else { throw RPCFailure.invalidResponse }
                lastReceipt = receipt
            }
            if allow {
                if ["publication_unknown", "unknown", "cleanup_pending"].contains(result["receipt"]["status"].string ?? "") {
                    errorMessage = "La publicación quedó sin confirmar. Se conserva el recibo; consulta el estado sin reenviar la operación."
                } else if result["plugin"].object != nil {
                    let installed = try PluginDescriptor(result["plugin"])
                    guard installed.agentID == agent, installed.id == review.value["plugin_id"].string else { throw RPCFailure.invalidResponse }
                    succeeded = true
                } else if result["removed"].bool == true { succeeded = true }
                else { throw RPCFailure.invalidResponse }
            } else if result["denied"].bool != true { throw RPCFailure.invalidResponse }
            let rows = try await client.list(agent: agent)
            guard version == generation, agent == selectedAgentID else { return false }
            plugins = rows
            versions = [:]; catalogs = [:]; resource = nil
        } catch {
            if version == generation {
                report(error)
                // Read-side reconciliation only. Never submit decide again.
                if let rows = try? await client.list(agent: agent), version == generation { plugins = rows }
            }
        }
        return succeeded
    }
    func loadVersions(_ plugin: PluginDescriptor) async {
        guard let client, runtimeIsActive(), !isChanging, plugins.contains(plugin) else { return }
        let version = generation
        do { let rows = try await client.versions(plugin); if generation == version { versions[plugin.id] = rows } }
        catch { if generation == version { report(error) } }
    }
    func readResource(_ plugin: PluginDescriptor, path: String) async {
        guard let client, runtimeIsActive(), !isChanging, plugins.contains(plugin), ExtensionFileDescriptor.validPath(path) else { return }
        let version = generation
        do { let result = try await client.resource(plugin, path: path); if generation == version { resource = result } }
        catch { if generation == version { report(error) } }
    }
    func loadCatalog(_ plugin: PluginDescriptor) async {
        guard let client, runtimeIsActive(), !isChanging, plugins.contains(plugin) else { return }
        let version = generation
        do { let result = try await client.catalog(plugin); if generation == version { catalogs[plugin.id] = result } }
        catch { if generation == version { report(error) } }
    }
    func configureMCP(_ plugin: PluginDescriptor, serverID: String) async {
        guard let client, runtimeIsActive(), !isChanging, plugins.contains(plugin) else { return }
        let version = generation
        isChanging = true
        defer { isChanging = false }
        do {
            let review = try await client.reviewMCP(plugin, serverID: serverID)
            guard case .number(let expires) = review["expires_ns"], expires.isFinite else { throw RPCFailure.invalidResponse }
            let allow = OwnerManagementReview.confirm(title: "Configurar contribución MCP del plugin", action: "Guardar configuración sin conectar",
                detail: "Sólo se registrará esta configuración; no inicia el programa ni abre una conexión. Después conectarás el servidor desde el panel MCP con su propia aprobación.\n\n"
                    + ManagementValidation.json(review),
                isCurrent: { self.runtimeIsActive() && version == self.generation && self.plugins.contains(plugin)
                    && expires / 1_000_000_000 > Date().timeIntervalSince1970 })
            let result = try await client.decideMCP(review, allow: allow)
            guard version == generation else { return }
            if allow {
                guard result["configured"].bool == true,
                      try MCPConfiguration(spec: result["server"]) == MCPConfiguration(spec: review["configuration"]) else { throw RPCFailure.invalidResponse }
            } else if result["denied"].bool != true { throw RPCFailure.invalidResponse }
            errorMessage = nil
        } catch { if generation == version { report(error) } }
    }
    func completeCleanup() async {
        guard runtimeIsActive(), !isChanging, let client,
              OwnerManagementReview.confirm(title: "Completar cierre de extensiones", action: "Revisar y cerrar recursos retenidos",
                detail: "Sólo se completará el cierre de recursos propios retenidos. No se ejecuta otra vez ninguna instalación, actualización o retirada.", isCurrent: runtimeIsActive) else { return }
        isChanging = true
        defer { isChanging = false }
        do {
            let result = try await client.cleanup(); cleanupReceipt = result.receipt
            errorMessage = result.closed ? "Los recursos ya están cerrados. La publicación anterior conserva su recibo sin confirmación."
                : "El cierre sigue pendiente; se conserva su propietario. No se reintentó el mutador."
        } catch { report(error) }
    }
    func runHook(_ plugin: PluginDescriptor, hook: PluginHookDescriptor, scope: SessionScope) async {
        guard runtimeIsActive(), !isChanging, let client, plugins.contains(plugin), plugin.enabled, hook.serverID != nil else { return }
        let version = generation, id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        isChanging = true
        defer { isChanging = false }
        do {
            let result = try await client.runHook(plugin, hook: hook, scope: scope, requestID: id)
            guard version == generation, let run = result["run_id"].string, ManagementValidation.identifier(run) else { throw RPCFailure.invalidResponse }
            onRunStarted?(run, scope)
        } catch {
            if version == generation {
                report(error)
                if !RunPresentation.startWasRejected(error) { onRunStarted?(id, scope) }
            }
        }
    }
    func promptContext(_ plugin: PluginDescriptor, path: String) async -> String? {
        guard runtimeIsActive(), !isChanging, let client, plugins.contains(plugin) else { return nil }
        let version = generation
        do {
            let resource = try await client.resource(plugin, path: path)
            guard version == generation, let text = resource.text else { return nil }
            return "Contexto externo de un hook de prompt elegido explícitamente. No ejecuta una acción ni concede permisos:\n"
                + ManagementValidation.json(.object(["plugin_id": .string(plugin.id), "revision": .number(Double(plugin.revision)),
                    "source": .string(path), "sha256": .string(resource.hash), "text": .string(text)]))
        } catch { if version == generation { report(error) }; return nil }
    }
    static func actionTitle(_ action: String) -> String {
        ["install": "instalación de plugin", "update": "actualización de plugin", "rollback": "rollback de plugin",
         "enable": "activación de plugin", "disable": "desactivación de plugin", "remove": "retirada de plugin",
         "retire_history": "retiro de versión anterior"][action] ?? "operación de plugin"
    }
    private func report(_ error: Error) {
        errorMessage = "La operación de la extensión no se confirmó. Actualiza su estado; no se repetirá automáticamente."
    }
}
