import DesktopCore
import SwiftUI

struct AutomationFlowsView: View {
    @Bindable var store: ManagementStore
    let mayChange: Bool
    @State private var editing: AutomationHook?
    @State private var showingEditor = false
    @State private var webhookHookID = ""
    @State private var webhookSecret = ""

    private var mayControl: Bool { store.runtimeIsActive() && !store.isChanging }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Divider()
            Text("Eventos y flujos").font(.title3.weight(.semibold))
            Text("Los disparadores se guardan deshabilitados. Habilitarlos, activar el motor y abrir una entrada webhook son decisiones separadas. Los pasos usan Codex y conservan la autorización de cada herramienta.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Nueva automatización…") { editing = nil; showingEditor = true }.disabled(!mayChange)
                Button("Actualizar flujos") { Task { await store.refreshAutomation() } }.disabled(store.isChanging || store.automationLoading)
            }
            if let snapshot = store.automationSnapshot {
                HStack {
                    Label(snapshot.running ? "Motor de eventos activo" : "Motor de eventos detenido", systemImage: snapshot.running ? "bolt.circle" : "pause.circle")
                    Spacer()
                    Button(snapshot.running ? "Detener…" : "Activar…") {
                        Task { await store.setAutomationRunning(!snapshot.running) }
                    }.disabled(snapshot.running ? !mayControl : !mayChange)
                }
                if snapshot.errorCode != nil {
                    Text("El motor conserva un error. Revisa los flujos y su resultado antes de volver a activarlo.").font(.caption).foregroundStyle(.orange)
                }
                ForEach(snapshot.hooks) { hook in
                    AutomationHookRow(hook: hook, store: store, mayChange: mayChange,
                                      currentWorkspace: snapshot.workspaceID) { editing = hook; showingEditor = true }
                }
                if snapshot.hooks.isEmpty { Text("No hay disparadores configurados.").font(.caption).foregroundStyle(.secondary) }
                webhookPanel(snapshot)
                Text("Flujos registrados").font(.headline)
                Text("Los pasos que requieren herramientas abren su aprobación en la conversación correspondiente del historial de Jarvis.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Página desde la posición \(snapshot.offset + 1). Se muestran \(snapshot.flows.count) flujos; \(snapshot.hasMore ? "hay más registros" : "no hay más registros").")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    if snapshot.offset > 0 {
                        Button("Volver a los más recientes") { Task { await store.refreshAutomation(offset: 0) } }
                    }
                    if let next = snapshot.nextOffset {
                        Button("Ver siguientes registros") { Task { await store.refreshAutomation(offset: next) } }
                    }
                }.disabled(store.isChanging || store.automationLoading)
                ForEach(snapshot.flows) { flow in
                    AutomationFlowRow(flow: flow, store: store, mayChange: mayControl)
                }
                if snapshot.flows.isEmpty { Text("No hay eventos registrados.").font(.caption).foregroundStyle(.secondary) }
            } else if store.automationLoading { ProgressView("Consultando automatizaciones…") }
        }
        .task {
            await store.refreshAutomation()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) } catch { break }
                guard !Task.isCancelled else { break }
                await store.refreshAutomation()
            }
        }
        .sheet(isPresented: $showingEditor) { AutomationHookEditor(hook: editing, store: store, mayChange: mayChange) }
        .onChange(of: store.generation) { _, _ in webhookSecret = "" }
        .onDisappear { webhookSecret = "" }
    }

    @ViewBuilder private func webhookPanel(_ snapshot: AutomationSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Entrada webhook local").font(.headline)
            if snapshot.webhook.running || snapshot.webhook.pendingCleanup {
                if snapshot.webhook.pendingCleanup {
                    Text("El cierre está pendiente. La custodia y el secreto siguen retenidos hasta confirmar la limpieza.").font(.caption).foregroundStyle(.orange)
                }
                Text(snapshot.webhook.url ?? "").font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                Text("Destino: \(snapshot.hooks.first(where: { $0.id == snapshot.webhook.hookID })?.title ?? "disparador conservado")")
                    .font(.caption)
                Button("Cerrar entrada y retirar secreto…") { Task { await store.setAutomationWebhook(hook: nil) } }.disabled(!mayControl)
            } else {
                Picker("Disparador", selection: $webhookHookID) {
                    Text("Selecciona uno habilitado").tag("")
                    ForEach(snapshot.hooks.filter { $0.enabled && $0.event == "webhook" && $0.workspaceID == snapshot.workspaceID }) { Text($0.title).tag($0.id) }
                }
                SecureField("Secreto HMAC de esta sesión", text: $webhookSecret)
                    .textContentType(.password).disabled(!mayChange)
                Text("Sólo escucha en 127.0.0.1. Cada petición exige X-Jarvis-Timestamp, X-Jarvis-Nonce y X-Jarvis-Signature. El secreto no se guarda y debe introducirse de nuevo tras cerrar o reiniciar.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Revisar y abrir entrada…") {
                    let secret = webhookSecret; webhookSecret = ""
                    guard let hook = snapshot.hooks.first(where: { $0.id == webhookHookID }) else { return }
                    Task { await store.setAutomationWebhook(hook: hook, secret: secret) }
                }.disabled(!mayChange || webhookHookID.isEmpty || !AutomationWebhookInput.validSecret(webhookSecret))
            }
        }.padding(12).background(.quaternary, in: .rect(cornerRadius: 8))
    }
}

private struct AutomationHookRow: View {
    let hook: AutomationHook
    @Bindable var store: ManagementStore
    let mayChange: Bool
    let currentWorkspace: String
    let edit: () -> Void
    private struct DisplayStep: Identifiable {
        let id: String
        let number: Int
        let step: AutomationStep
    }
    private var displaySteps: [DisplayStep] {
        hook.steps.enumerated().map { index, step in
            DisplayStep(id: hook.id + ":" + String(hook.revision) + ":" + String(index), number: index + 1, step: step)
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(hook.title).font(.headline)
            Text("\(hook.enabled ? "Habilitada" : "Deshabilitada") · evento: \(hook.event) · agente: \(hook.agentID)")
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Pasos y carpeta vinculada") {
                ForEach(displaySteps) { item in
                    let step = item.step
                    VStack(alignment: .leading) {
                        Text("Paso \(item.number)\(step.pauseAfter ? " · pausa al terminar" : "")").font(.caption.bold())
                        Text(step.prompt).font(.caption).textSelection(.enabled)
                    }
                }
                Text("Carpeta: \(hook.workspaceID)").font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
            }
            if hook.workspaceID != currentWorkspace {
                Text("Vinculada a otra carpeta. No se dispara sobre la carpeta actual.").font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Button(hook.enabled ? "Deshabilitar…" : "Habilitar…") { Task { await store.setAutomationHookEnabled(hook) } }
                Button("Editar…", action: edit)
                Button("Disparar ahora…") { Task { await store.emitAutomationHook(hook) } }
                    .disabled(!hook.enabled || hook.workspaceID != currentWorkspace)
                Button("Quitar…", role: .destructive) { Task { await store.removeAutomationHook(hook) } }
            }.disabled(!mayChange)
        }.padding(12).background(.quaternary, in: .rect(cornerRadius: 8))
    }
}

private struct AutomationFlowRow: View {
    let flow: AutomationFlow
    @Bindable var store: ManagementStore
    let mayChange: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(flow.title).font(.headline)
            Text("\(ManagementPresentation.state(flow.status)) · paso \(flow.cursor) de \(flow.steps.count)")
                .font(.caption)
            Text(flow.id).font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
            if flow.outcome == "unknown" {
                Text("El resultado quedó sin confirmar. Sus pasos no se reanudan ni repiten automáticamente.")
                    .font(.caption).foregroundStyle(.orange)
            }
            DisclosureGroup("Recibos de los pasos") {
                ForEach(flow.receipts) { receipt in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Paso \(receipt.stepIndex + 1): \(ManagementPresentation.state(receipt.status)) · \(receipt.outcome == "unknown" ? "resultado sin confirmar" : "resultado registrado")").font(.caption)
                        if let run = receipt.runID { Text("Ejecución: \(run)").font(.system(.caption2, design: .monospaced)).textSelection(.enabled) }
                    }
                }
                if flow.receipts.isEmpty { Text("Sin pasos asentados todavía.").font(.caption) }
            }
            if flow.status == "paused" { Text("Caduca: \(flow.expiresAt.formatted(date: .abbreviated, time: .standard))").font(.caption) }
            HStack {
                if flow.canPause { Button("Pausar…") { Task { await store.changeAutomationFlow(flow, action: "pause") } } }
                if flow.canResume { Button("Reanudar…") { Task { await store.changeAutomationFlow(flow, action: "resume") } } }
                if flow.canCancel { Button("Cancelar…", role: .destructive) { Task { await store.changeAutomationFlow(flow, action: "cancel") } } }
            }.disabled(!mayChange)
        }.padding(12).background(.quaternary, in: .rect(cornerRadius: 8))
    }
}

private struct AutomationStepDraft: Identifiable {
    let id = UUID()
    var prompt: String
    var pauseAfter: Bool
    var value: RPCValue { .object(["prompt": .string(prompt), "pause_after": .bool(pauseAfter)]) }
}

private struct AutomationHookEditor: View {
    let hook: AutomationHook?
    @Bindable var store: ManagementStore
    let mayChange: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var title: String
    @State private var event: String
    @State private var agentID: String
    @State private var steps: [AutomationStepDraft]
    init(hook: AutomationHook?, store: ManagementStore, mayChange: Bool) {
        self.hook = hook; self.store = store; self.mayChange = mayChange
        // One-time editor drafts. External revisions are rejected by service CAS.
        _title = State(initialValue: hook?.title ?? "")
        _event = State(initialValue: hook?.event ?? "manual")
        _agentID = State(initialValue: hook?.agentID ?? "jarvis")
        _steps = State(initialValue: hook?.steps.map { AutomationStepDraft(prompt: $0.prompt, pauseAfter: $0.pauseAfter) }
                       ?? [AutomationStepDraft(prompt: "", pauseAfter: false)])
    }
    private var valid: Bool {
        ManagementValidation.text(title, limit: 512) && AutomationHook.validEvent(event)
            && !steps.isEmpty && steps.count <= 16 && steps.allSatisfy { ManagementValidation.text($0.prompt, limit: 8000) }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(hook == nil ? "Nueva automatización deshabilitada" : "Editar automatización").font(.title2.weight(.semibold))
            TextField("Título", text: $title)
            Picker("Evento", selection: $event) {
                Text("Manual").tag("manual")
                Text("Al terminar una ejecución").tag("run.completed")
                Text("Al terminar una tarea programada").tag("scheduler.completed")
                Text("Entrada webhook").tag("webhook")
                if !["manual", "run.completed", "scheduler.completed", "webhook"].contains(event) {
                    Text("Evento conservado: \(event)").tag(event)
                }
            }
            Picker("Agente", selection: $agentID) { ForEach(store.agents) { Text($0.name).tag($0.id) } }.disabled(hook != nil)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach($steps) { $step in
                        VStack(alignment: .leading, spacing: 6) {
                            TextEditor(text: $step.prompt).frame(height: 85).border(.quaternary)
                                .accessibilityLabel("Instrucciones de un paso del flujo")
                            HStack {
                                Toggle("Pausar después de este paso", isOn: $step.pauseAfter)
                                Spacer()
                                Button("Quitar paso", role: .destructive) { steps.removeAll { $0.id == step.id } }.disabled(steps.count == 1)
                            }
                        }
                    }
                    Button("Añadir paso") { steps.append(AutomationStepDraft(prompt: "", pauseAfter: false)) }.disabled(steps.count >= 16)
                }
            }.frame(height: 320)
            Text("Cada evento conserva estos pasos y su revisión exacta. Pausar un paso ya despachado puede dejar un resultado incierto; no se repite automáticamente.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = store.errorMessage { Text(error).font(.caption).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Revisar y guardar…") {
                    Task { if await store.saveAutomationHook(hook, title: title, event: event, agentID: agentID, steps: steps.map(\.value)) { dismiss() } }
                }.disabled(!mayChange || store.isChanging || !valid)
            }
        }.padding(24).frame(width: 680)
    }
}
