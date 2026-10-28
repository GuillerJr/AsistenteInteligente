import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class ManagementStore {
    var agents: [AgentDescriptor] = []
    var selectedAgentID = "jarvis"
    var skills: [SkillDescriptor] = []
    var scheduler: SchedulerSnapshot?
    var occurrences: [ScheduleOccurrenceSnapshot] = []
    var processCatalog: ProcessCatalogSnapshot?
    var processOutput: [String: String] = [:]
    var errorMessage: String?
    var isChanging = false
    var isLoading = false
    var lastMutationReceipt: RPCValue?
    var extensionCleanupReceipt: RPCValue?
    @ObservationIgnored var client: ManagementClient?
    @ObservationIgnored private var scope: SessionScope?
    @ObservationIgnored var runtimeIsActive: @Sendable () -> Bool = { false }
    @ObservationIgnored var generation = 0
    @ObservationIgnored private var historyRequest = 0

    func configure(client: LocalRPCClient) { configure(ManagementClient(transport: client), runtimeIsActive: { client.isActive }) }
    func configure(_ client: ManagementClient, runtimeIsActive: @escaping @Sendable () -> Bool) {
        generation += 1; self.client = client; self.runtimeIsActive = runtimeIsActive
    }
    func close() async { generation += 1; await client?.close() }
    func setContext(_ value: SessionScope?) {
        guard scope != value else { return }
        generation += 1; scope = value; processCatalog = nil; processOutput = [:]; polled = [:]
    }
    func refresh() async throws {
        guard !isChanging, !isLoading, let client else { return }
        isLoading = true
        defer { isLoading = false }
        let version = generation
        let agents = try await client.agents()
        let schedules = try await client.schedules()
        guard version == generation else { return }
        self.agents = agents; scheduler = schedules
        if !agents.contains(where: { $0.id == selectedAgentID }) { selectedAgentID = "jarvis" }
    }
    func refreshFromUI() async {
        do { try await refresh(); errorMessage = nil } catch { report(error) }
    }
    func loadSkills() async {
        guard !isChanging, let client else { return }
        let agent = selectedAgentID, version = generation
        do {
            let rows = try await client.skills(agentID: agent)
            guard agent == selectedAgentID, version == generation else { return }
            skills = rows; errorMessage = nil
        } catch { if version == generation { report(error) } }
    }
    func inspectSkill(path: String) async -> SkillPreviewSnapshot? {
        guard !isChanging, let client else { return nil }
        do { let preview = try await client.inspect(path: path); errorMessage = nil; return preview }
        catch { report(error); return nil }
    }
    func skillText(_ skill: SkillDescriptor) async -> String? {
        guard let client, skillReadIsCurrent(skill, generation: generation) else { return nil }
        let version = generation
        do {
            let text = try await client.skillText(skill)
            return skillReadIsCurrent(skill, generation: version) ? text : nil
        } catch { if skillReadIsCurrent(skill, generation: version) { report(error) }; return nil }
    }
    func loadHistory(_ schedule: ScheduleDescriptor) async {
        guard let client else { return }
        historyRequest += 1
        let request = historyRequest
        occurrences = []
        do {
            let rows = try await client.history(scheduleID: schedule.id)
            guard request == historyRequest else { return }
            occurrences = rows; errorMessage = nil
        } catch { if request == historyRequest { report(error) } }
    }
    func loadProcesses() async {
        guard let client, let scope else { processCatalog = nil; return }
        let version = generation
        do {
            let catalog = try await client.processes(scope: scope)
            guard version == generation else { return }
            processCatalog = catalog
            let identities = Set(catalog.processes.map(\.id))
            polled = polled.filter { identities.contains($0.key) }
            processOutput = processOutput.filter { identities.contains($0.key) }
            errorMessage = nil
        } catch { if version == generation { report(error) } }
    }
    func poll(_ process: ProcessSnapshot, more: Bool) async {
        guard let client, let scope else { return }
        let version = generation
        do {
            let snapshot = try await client.poll(scope: scope, processID: process.id,
                                                  cursor: more ? process.nextCursor : 0)
            guard version == generation else { return }
            let text = (more ? processOutput[process.id] ?? process.output : "") + snapshot.output
            guard text.utf8.count <= 1_048_576 else { throw RPCFailure.oversized }
            processOutput[process.id] = text
            if let previous = processCatalog {
                // Refresh status/cursors through the same scoped list. Preserve
                // expanded output separately; reading it grants no capability.
                let updated = try await client.processes(scope: scope)
                guard version == generation else { return }
                processCatalog = updated
                if previous.available != updated.available { processOutput = [:] }
            }
            polled[process.id] = snapshot
        } catch { if version == generation { report(error) } }
    }
    var polled: [String: ProcessSnapshot] = [:]

    func saveAgent(_ agent: AgentDescriptor?, name: String, instructions: String) async -> Bool {
        guard ManagementValidation.text(name, limit: 256), ManagementValidation.text(instructions, limit: 8192, empty: true) else {
            errorMessage = "El nombre o las instrucciones superan el límite permitido."; return false
        }
        var params = agent?.revisionParams ?? [:]
        params["name"] = .string(name); params["instructions"] = .string(instructions)
        return await change(agent == nil ? "agents.create" : "agents.update", params,
                            title: agent == nil ? "Crear agente" : "Actualizar agente", action: "Guardar agente")
    }
    func deleteAgent(_ agent: AgentDescriptor) async {
        guard agent.id != "jarvis" else { return }
        _ = await change("agents.delete", agent.revisionParams, title: "Borrar agente «\(agent.name)»",
                         action: "Borrar agente")
    }
    func importSkill(_ preview: SkillPreviewSnapshot, agentID: String) async -> Bool {
        await change("skills.import", ["agent_id": .string(agentID), "path": .string(preview.sourcePath),
                                       "expected_sha256": .string(preview.hash), "enabled": .bool(false)],
                     title: "Importar skill deshabilitada", action: "Importar esta versión")
    }
    func saveSkill(_ skill: SkillDescriptor, text: String) async -> Bool {
        guard ManagementValidation.text(text, limit: 32768) else { errorMessage = "El manifiesto no es válido o supera el límite."; return false }
        var params = skill.revisionParams; params["text"] = .string(text)
        return await change("skills.update", params, title: "Revisar nueva versión de «\(skill.name)»", action: "Guardar esta versión")
    }
    func enableSkill(_ skill: SkillDescriptor) async {
        let content: String?
        if !skill.enabled {
            content = await skillText(skill)
            guard content != nil else { return }
        } else { content = nil }
        var params = skill.revisionParams; params["enabled"] = .bool(!skill.enabled)
        _ = await change("skills.enable", params, title: skill.enabled ? "Deshabilitar skill" : "Habilitar skill", action: "Confirmar cambio",
                         detail: ManagementValidation.json(.object(params)) + (content.map { "\n\nManifiesto de esta versión:\n" + $0 } ?? ""))
    }
    func removeSkill(_ skill: SkillDescriptor) async {
        _ = await change("skills.remove", skill.revisionParams, title: "Quitar skill «\(skill.name)»", action: "Quitar skill")
    }
    func saveSchedule(_ schedule: ScheduleDescriptor?, title: String, prompt: String,
                      agentID: String, spec: RPCValue) async -> Bool {
        guard ManagementValidation.text(title, limit: 512), ManagementValidation.text(prompt, limit: 8000) else {
            errorMessage = "El título o las instrucciones de la tarea no son válidos."; return false
        }
        var params = schedule?.revisionParams ?? ["agent_id": .string(agentID), "enabled": .bool(false)]
        params["title"] = .string(title); params["prompt"] = .string(prompt); params["spec"] = spec
        return await change(schedule == nil ? "schedules.create" : "schedules.update", params,
                            title: schedule == nil ? "Crear tarea deshabilitada" : "Actualizar tarea", action: "Guardar tarea")
    }
    func enableSchedule(_ schedule: ScheduleDescriptor) async {
        var params = schedule.revisionParams; params["enabled"] = .bool(!schedule.enabled)
        _ = await change("schedules.update", params,
                         title: schedule.enabled ? "Pausar tarea" : "Habilitar tarea", action: "Confirmar cambio",
                         detail: scheduleReview(schedule) + "\n\nCambio exacto:\n" + ManagementValidation.json(.object(params)))
    }
    func removeSchedule(_ schedule: ScheduleDescriptor) async {
        _ = await change("schedules.remove", schedule.revisionParams, title: "Quitar tarea «\(schedule.title)»", action: "Quitar tarea",
                         detail: scheduleReview(schedule))
    }
    func startScheduler() async {
        guard let snapshot = scheduler else { return }
        _ = await change("scheduler.start", ["configuration_hash": .string(snapshot.configurationHash)],
                         title: "Activar programador", action: "Activar estas tareas",
                         detail: "Se enviarán los prompts habilitados a Codex cuando corresponda. Las herramientas seguirán pidiendo aprobación.\n\n" + ManagementValidation.json(snapshot.rawSchedules))
    }
    func stopScheduler() async {
        _ = await change("scheduler.stop", [:], title: "Detener programador", action: "Detener",
                         detail: "Se detendrá la supervisión y se cancelarán sus ejecuciones activas. Un efecto ya despachado puede quedar sin confirmar.")
    }
    private func scheduleReview(_ row: ScheduleDescriptor) -> String {
        ManagementValidation.json(.object(["schedule_id": .string(row.id), "title": .string(row.title),
            "prompt": .string(row.prompt), "agent_id": .string(row.agentID), "workspace_id": .string(row.workspaceID),
            "spec": row.spec, "expected_revision": .number(Double(row.revision))]))
    }
    func change(_ method: String, _ params: [String: RPCValue], title: String,
                        action: String, detail: String? = nil) async -> Bool {
        guard runtimeIsActive(), !isChanging, let client else { return false }
        isChanging = true; generation += 1
        defer { isChanging = false }
        guard OwnerManagementReview.confirm(title: title, action: action,
            detail: detail ?? "Revisa esta operación exacta. No autoriza herramientas ni ejecución de scripts.\n\n" + ManagementValidation.json(.object(params)), isCurrent: runtimeIsActive) else { return false }
        var success = false
        do {
            let result = try await client.mutate(method, params: params)
            if let receipt = result.object?["receipt"] {
                guard receipt.object != nil, try JSONEncoder().encode(receipt).count <= 65536 else { throw RPCFailure.invalidResponse }
                lastMutationReceipt = receipt
            }
            if method.hasPrefix("skills."), result["skill"].object == nil,
               result["receipt"]["status"].string == "publication_unknown" {
                errorMessage = "La publicación quedó sin confirmar. Se conserva su recibo; actualiza el estado y no reenvíes la operación."
            } else {
            if method.hasPrefix("agents."), method != "agents.delete" { _ = try AgentDescriptor(result["agent"]) }
            else if method.hasPrefix("skills."), method != "skills.remove" { _ = try SkillDescriptor(result["skill"]) }
            else if method.hasPrefix("schedules."), method != "schedules.remove" { _ = try ScheduleDescriptor(result["schedule"]) }
            else {
                let key = method == "agents.delete" ? "deleted" : method == "scheduler.start" || method == "scheduler.stop" ? "running" : "removed"
                guard result[key].bool == (method != "scheduler.stop") else { throw RPCFailure.invalidResponse }
            }
            errorMessage = nil; success = true
            }
        } catch { report(error) }
        // Read-side reconciliation only; a failed mutation is never resent.
        do {
            agents = try await client.agents(); scheduler = try await client.schedules()
            if !agents.contains(where: { $0.id == selectedAgentID }) { selectedAgentID = "jarvis" }
            skills = try await client.skills(agentID: selectedAgentID)
        } catch { if errorMessage == nil { report(error) } }
        return success
    }
    private func report(_ error: Error) { errorMessage = ManagementPresentation.errorMessage(error) }
}

@MainActor enum OwnerManagementReview {
    static func confirm(title: String, action: String, detail: String, isCurrent: () -> Bool = { true }) -> Bool {
        guard isCurrent() else { return false }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = "Esta decisión corresponde al dueño de Jarvis."
        alert.addButton(withTitle: action); alert.addButton(withTitle: "Cancelar")
        alert.buttons.first?.keyEquivalent = ""; alert.buttons.last?.keyEquivalent = "\r"
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 580, height: 260))
        scroll.hasVerticalScroller = true; scroll.hasHorizontalScroller = false
        scroll.borderType = .bezelBorder
        let text = NSTextView(frame: scroll.bounds)
        text.isEditable = false; text.isSelectable = true; text.isRichText = false
        text.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        text.isVerticallyResizable = true; text.isHorizontallyResizable = false
        text.autoresizingMask = .width
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        text.textContainer?.widthTracksTextView = true
        text.textContainer?.containerSize = NSSize(width: scroll.bounds.width, height: CGFloat.greatestFiniteMagnitude)
        text.string = detail; text.setAccessibilityLabel("Operación exacta para revisión del dueño")
        text.sizeToFit()
        scroll.documentView = text; alert.accessoryView = scroll
        return alert.runModal() == .alertFirstButtonReturn && isCurrent()
    }
}
