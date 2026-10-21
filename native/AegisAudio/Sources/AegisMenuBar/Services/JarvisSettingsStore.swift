import AegisAudioCore
import AppKit
import Foundation
import Observation

private struct JarvisSettingsError: Error { let code: String }

@MainActor @Observable
final class JarvisSettingsStore {
    var snapshot: JarvisSettingsSnapshot?
    var loading = false
    var busy = false
    var message: String?
    var failed = false
    var operationState = "idle"
    var memoryHits: [JarvisMemoryHit] = []
    var standingGrants: [JarvisStandingGrant] = []
    var scheduledTasks: [JarvisScheduledTask] = []
    var goals: [JarvisGoal] = []
    var personality: JarvisPersonality?
    var modelDraft: JarvisModelPreferences?
    var extensionsSnapshot: JarvisExtensionsSnapshot?
    var extensionsLoading = false
    var extensionsError: String?
    var foreignCatalogue: JarvisForeignPlugins?
    var restartRequired: Bool {
        snapshot?.restart_required == true || extensionsSnapshot?.restart_required == true
    }
    @ObservationIgnored private var monitor: Task<Void, Never>?

    func refresh() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            snapshot = try await request("settings.status", as: JarvisSettingsSnapshot.self)
            if failed && message?.hasPrefix("No se pudo leer la configuración") == true {
                clearMessage()
            }
        } catch {
            fail("No se pudo leer la configuración. Comprueba que el servicio esté conectado.")
        }
        await refreshExtensions()
    }

    func refreshExtensions() async {
        guard !extensionsLoading else { return }
        extensionsLoading = true
        defer { extensionsLoading = false }
        do {
            extensionsSnapshot = try await request("extensions.status", as: JarvisExtensionsSnapshot.self)
            extensionsError = nil
        } catch {
            extensionsSnapshot = nil
            extensionsError = "No se pudo leer el catálogo de extensiones. Actualiza el estado del servicio."
        }
    }

    func skillDetails(_ skill: JarvisExtensionSkill) async -> JarvisSkillDetails? {
        await extensionPreview("extensions.skill.details", payload: [
            "skill_id": skill.id, "content_sha256": skill.content_sha256,
        ], as: JarvisSkillDetails.self)
    }

    func skillPreview(path: String) async -> JarvisSkillImportPreview? {
        await extensionPreview("extensions.skill.preview", payload: ["path": path],
                               as: JarvisSkillImportPreview.self)
    }

    func pluginPreview(path: String) async -> JarvisPluginImportPreview? {
        await extensionPreview("extensions.plugin.preview", payload: ["path": path],
                               as: JarvisPluginImportPreview.self)
    }

    func pluginDetails(_ plugin: JarvisExtensionPlugin) async -> JarvisPluginImportPreview? {
        guard let checksum = plugin.checksum_sha256 else { return nil }
        return await extensionPreview("extensions.plugin.details", payload: [
            "plugin_id": plugin.id, "checksum_sha256": checksum,
        ], as: JarvisPluginImportPreview.self)
    }

    private func extensionPreview<T: Decodable>(_ method: String, payload: [String: Any],
                                                as type: T.Type) async -> T? {
        guard !busy else { return nil }
        busy = true
        defer { busy = false }
        do { return try await request(method, payload: payload, as: type) }
        catch { fail(extensionErrorMessage(error, fallback: "No se pudo revisar la extensión. Comprueba el formato y los límites del archivo.")) }
        return nil
    }

    private func extensionErrorMessage(_ error: Error, fallback: String) -> String {
        guard let error = error as? JarvisSettingsError else { return fallback }
        switch error.code {
        case "extensions_busy": return "Jarvis tiene una tarea en curso. Espera a que termine para cambiar extensiones."
        case "extensions_source_changed", "source_changed": return "El contenido cambió desde la revisión. Actualiza y revisa la versión actual."
        case "extensions_restart_required": return "Aplica los cambios y reinicia antes de modificar esta skill."
        case "extensions_policy_unavailable": return "La política local no permite guardar más cambios. Revisa su integridad y capacidad antes de importar."
        case "source_contains_secret_material": return "El archivo contiene posibles credenciales. Retíralas antes de importar."
        case "extensions_executable_unsupported", "skill_executable_unsupported": return "Esta skill requiere código que Jarvis no ejecuta. Adáptala a instrucciones declarativas."
        default: return fallback
        }
    }

    func perform(_ method: String, payload: [String: Any] = [:], success: String) async {
        guard !busy else { return }
        busy = true
        failed = false
        message = nil
        do {
            let data = try await call(method, payload: payload)
            let operation = try? JSONDecoder().decode(JarvisSettingsOperation.self, from: data)
            if let operation, operation.state == "starting" {
                operationState = operation.state
                monitor = Task { [weak self] in await self?.monitorOperation(success: success) }
            } else {
                busy = false
                message = success
                await refresh()
            }
        } catch {
            busy = false
            fail(extensionErrorMessage(error, fallback: "No se guardó el cambio. Revisa los datos, los permisos y el estado del servicio."))
        }
    }

    func saveModels(_ preferences: JarvisModelPreferences) async {
        do {
            let data = try JSONEncoder().encode(preferences)
            let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            await perform("settings.models.save", payload: payload,
                          success: "Modelos guardados. Aplica los cambios para reiniciar el servicio.")
        } catch { fail("No se pudo guardar la selección de modelos.") }
    }

    func saveBrowserAutomation(enabled: Bool, mode: String) async {
        await perform("settings.browser.save", payload: [
            "enabled": enabled, "mode": mode, "confirm": true,
        ], success: "Preferencias del navegador guardadas. Se usarán en la próxima tarea.")
    }

    func cancelOperation() async {
        do {
            _ = try await call("settings.cancel")
            monitor?.cancel()
            busy = false
            operationState = "idle"
            message = "Operación cancelada. Puedes aplicar los cambios para restablecer las conexiones."
            await refresh()
        } catch { fail("No se pudo cancelar la operación.") }
    }

    func searchMemories(_ query: String) async {
        guard let snapshot, !busy, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        busy = true
        defer { busy = false }
        do {
            let result = try await request("memory.search", payload: [
                "namespace": snapshot.memory_namespace, "query": query, "limit": 10,
            ], as: JarvisMemoryResults.self)
            memoryHits = result.hits
            failed = false
            message = result.hits.isEmpty ? "No se encontraron recuerdos para esta búsqueda." : nil
        } catch { fail("No se pudo consultar la memoria.") }
    }

    func deleteMemory(_ hit: JarvisMemoryHit) async {
        guard let snapshot else { return }
        await perform("memory.delete", payload: ["namespace": snapshot.memory_namespace,
                                                "memory_id": hit.memory_id],
                      success: "Recuerdo eliminado.")
        if !failed { memoryHits.removeAll { $0.id == hit.id } }
    }

    func loadStandingGrants() async {
        do {
            standingGrants = try await request("jobs.grants.list", as: JarvisStandingGrants.self)
                .grants
        } catch { fail("No se pudieron cargar los permisos permanentes.") }
    }

    func revokeStandingGrant(_ grant: JarvisStandingGrant) async {
        await perform("jobs.grants.revoke", payload: ["tool_name": grant.tool_name],
                      success: "Jarvis volverá a pedir tu aprobación para esta herramienta.")
        if !failed { standingGrants.removeAll { $0.id == grant.id } }
    }

    func loadScheduledTasks() async {
        do {
            scheduledTasks = try await request("schedules.list", as: JarvisScheduledTasks.self).tasks
        } catch { fail("No se pudieron cargar las tareas programadas.") }
    }

    func setScheduledTask(_ task: JarvisScheduledTask, enabled: Bool) async {
        await perform("schedules.set_enabled", payload: ["task_id": task.task_id, "enabled": enabled],
                      success: enabled ? "Tarea reanudada." : "Tarea pausada.")
        await loadScheduledTasks()
    }

    func runScheduledTask(_ task: JarvisScheduledTask) async {
        await perform("schedules.run_now", payload: ["task_id": task.task_id],
                      success: "Ejecutando «\(task.title)». Te avisaré con el resultado.")
    }

    func deleteScheduledTask(_ task: JarvisScheduledTask) async {
        await perform("schedules.delete", payload: ["task_id": task.task_id],
                      success: "Tarea borrada.")
        if !failed { scheduledTasks.removeAll { $0.id == task.id } }
    }

    func loadGoals() async {
        do {
            goals = try await request("goals.list", as: JarvisGoals.self).goals
        } catch { fail("No se pudieron cargar los objetivos.") }
    }

    func setGoal(_ goal: JarvisGoal, running: Bool) async {
        await perform(running ? "goals.resume" : "goals.stop", payload: ["goal_id": goal.goal_id],
                      success: running ? "Jarvis retomó el objetivo." : "Objetivo detenido.")
        await loadGoals()
    }

    func deleteGoal(_ goal: JarvisGoal) async {
        await perform("goals.delete", payload: ["goal_id": goal.goal_id], success: "Objetivo borrado.")
        if !failed { goals.removeAll { $0.id == goal.id } }
    }

    func loadPersonality() async {
        do { personality = try await request("personality.get", as: JarvisPersonality.self) }
        catch { fail("No se pudo cargar la personalidad.") }
    }

    func savePersonality(preset: String, custom: String) async {
        await perform("personality.save", payload: ["preset": preset, "custom": custom],
                      success: "Jarvis usará esta personalidad desde la próxima respuesta.")
        await loadPersonality()
    }

    /// Codex and Claude plugins: the catalogue, installs and marketplaces.
    func foreignPlugins(_ method: String = "plugins.foreign.status", payload: [String: Any] = [:],
                        success: String? = nil) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let status = try JSONDecoder().decode(JarvisForeignPlugins.self, from: await call(
                method, payload: payload, timeout: method.hasSuffix("marketplace.add") ? 55 : 20))
            foreignCatalogue = status
            if let error = status.error { fail(error) }
            else if let success { message = success; failed = false }
        } catch { fail("No se pudo leer los complementos de Codex y Claude.") }
    }

    func clearMessage() { message = nil; failed = false }

    func fail(_ value: String) { message = value; failed = true }

    private func monitorOperation(success: String) async {
        var openedURL = false
        do {
            while !Task.isCancelled {
                let operation = try await request("settings.operation", as: JarvisSettingsOperation.self)
                operationState = operation.state
                if let text = operation.url, !openedURL, let url = URL(string: text),
                   url.scheme == "https", url.host == "accounts.google.com" {
                    openedURL = true
                    NSWorkspace.shared.open(url)
                }
                switch operation.state {
                case "done":
                    message = success
                    busy = false
                    await refresh()
                    return
                case "error", "cancelled":
                    busy = false
                    fail(operation.reason == "google_account_mismatch"
                         ? "Google autorizó otra cuenta. Vuelve a conectar usando el correo indicado."
                         : operation.reason == "browser_dependencies_unavailable"
                         ? "Faltan los componentes revisados del navegador o cambiaron. Reinstala los peers MCP de Jarvis y revisa de nuevo el perfil."
                         : operation.reason == "browser_schema_incompatible"
                         ? "La versión del navegador MCP no coincide con las herramientas revisadas. No se cambió la conexión."
                         : operation.reason == "browser_profile_capacity"
                         ? "Se alcanzó el límite de perfiles preparados. Revisa los perfiles locales antes de crear otro."
                         : operation.reason?.hasPrefix("model_") == true
                         ? "El modelo no superó la prueba de conexión o de capacidades. Revisa su ID y la clave de NVIDIA."
                         : "La operación no se completó. Revisa la conexión o vuelve a intentarlo.")
                    await refresh()
                    return
                default: break
                }
                try await Task.sleep(for: .seconds(1))
            }
        } catch {
            if !Task.isCancelled {
                busy = false
                fail("Se perdió la conexión durante la operación. Actualiza el estado antes de continuar.")
            }
        }
    }

    private func request<T: Decodable>(_ method: String, payload: [String: Any] = [:],
                                      as type: T.Type) async throws -> T {
        try JSONDecoder().decode(type, from: await call(method, payload: payload))
    }

    private func call(_ method: String, payload: [String: Any] = [:],
                      timeout: TimeInterval = 8) async throws -> Data {
        let input = try JSONSerialization.data(withJSONObject: payload)
        return try await Task.detached(priority: .userInitiated) {
            let decoded = try JSONSerialization.jsonObject(with: input) as? [String: Any] ?? [:]
            let client = try LocalIPCClient(secretStore: MacOSIPCSecretStore(), timeoutSeconds: timeout)
            let response = try client.call(method: method, payload: decoded)
            guard response.ok else { throw JarvisSettingsError(code: response.errorCode ?? "settings_unavailable") }
            return try JSONSerialization.data(withJSONObject: response.payload)
        }.value
    }
}
