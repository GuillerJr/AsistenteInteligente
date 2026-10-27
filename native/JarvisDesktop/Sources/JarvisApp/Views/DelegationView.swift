import DesktopCore
import SwiftUI

struct DelegationView: View {
    @Bindable var store: ChatStore
    @State private var objective = ""
    @State private var timeout = 120
    // These are editable form drafts, not a copy of parent execution state.
    @State private var children = [DelegatedTaskSpec(agentID: "jarvis", agentRevision: 1)]
    private var steps: Int { children.reduce(0) { $0 + $1.maxSteps } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Delegación y objetivos").font(.title3.bold())
                Text("Cada tarea usa una conversación nueva. Sus herramientas requieren aprobaciones propias; los resultados del modelo se presentan como reportes.")
                    .font(.callout).foregroundStyle(.secondary)
                Button("Consultar objetivos") { Task { await store.refreshDelegation() } }
                    .disabled(store.delegation.isChanging || store.delegation.isRefreshing || !store.ready)
                if store.delegation.isRefreshing || store.delegation.isChanging { ProgressView() }
                if !store.delegation.goals.isEmpty {
                    Menu("Objetivos registrados") {
                        ForEach(store.delegation.goals) { goal in
                            Button(goal.objective) { Task { await store.delegation.select(goal) } }
                                .disabled(store.delegation.blocksInteraction)
                        }
                    }
                    if store.delegation.hasMoreGoals {
                        Button("Consultar más objetivos") { Task { await store.delegation.loadMore() } }
                            .disabled(store.delegation.isRefreshing || store.delegation.isChanging)
                    }
                }
                if let goal = store.delegation.goal {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(goal.objective).font(.headline)
                        Text(DelegationPresentation.status(goal.status)).font(.callout)
                        if goal.parentOutcome == "unknown" { Text("El resultado del padre quedó sin confirmar.").font(.caption).foregroundStyle(.orange) }
                        Text("\(goal.reservedSteps)/\(goal.maxSteps) pasos reservados · \(goal.timeoutSeconds) s de plazo")
                            .font(.caption).foregroundStyle(.secondary)
                        Text(goal.id).font(.caption.monospaced()).textSelection(.enabled)
                        if !goal.settled {
                            Text("La conversación espera el cierre y los recibos de todas las tareas.").font(.caption)
                            Button("Cancelar objetivo y tareas", role: .destructive) { Task { await store.delegation.cancel() } }
                                .disabled(store.delegation.isChanging)
                        }
                        ForEach(goal.children) { child in
                            DelegatedChildView(store: store, child: child)
                        }
                        if goal.snapshotTruncated {
                            Text("Esta vista es un resumen acotado. Abre las conversaciones hijas para consultar sus registros completos.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let error = goal.errorCode { Text(error).font(.caption).foregroundStyle(.orange) }
                    }.padding(12).background(.quaternary, in: .rect(cornerRadius: 12))
                }
                if let error = store.delegation.errorMessage { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
                if let notice = store.delegation.steeringNotice { Text(notice).font(.caption).textSelection(.enabled) }
                Divider()
                Text("Preparar otro objetivo").font(.headline)
                TextField("Objetivo común", text: $objective, axis: .vertical).lineLimit(2...4)
                ForEach($children) { $child in
                    DelegatedTaskEditor(child: $child, agents: store.management.agents,
                                        mayRemove: children.count > 1) {
                        children.removeAll(where: { $0.id == child.id })
                    }
                }
                Button("Añadir tarea") {
                    let agent = store.management.agents.first(where: { $0.id == "jarvis" })
                    children.append(DelegatedTaskSpec(agentID: agent?.id ?? "jarvis", agentRevision: agent?.revision ?? 1))
                }.disabled(children.count >= 4 || store.delegation.blocksInteraction)
                Stepper("Plazo: \(timeout) segundos", value: $timeout, in: 10...3600, step: 10)
                Text("Presupuesto total: \(steps) pasos. Cada paso es una consulta del agente al proveedor.")
                    .font(.caption).foregroundStyle(steps <= 100 ? Color.secondary : .red)
                Button("Revisar delegación…") {
                    Task { await store.startDelegation(objective: objective, children: children, timeout: timeout) }
                }.disabled(!store.canChangeSession || !store.connected || !ManagementValidation.text(objective, limit: 512)
                    || children.contains(where: { child in !child.valid || !store.management.agents.contains(where: { $0.id == child.agentID && $0.revision == child.agentRevision }) }) || steps > 100)
            }.padding(20)
        }
        .task(id: store.selected?.scope) { await store.refreshDelegation() }
        .onChange(of: store.management.agents, initial: true) { _, agents in
            children = children.map { child in
                var current = child
                if let agent = agents.first(where: { $0.id == child.agentID }) { current.agentRevision = agent.revision }
                return current
            }
        }
    }
}

private struct DelegatedTaskEditor: View {
    @Binding var child: DelegatedTaskSpec
    let agents: [AgentDescriptor]
    let mayRemove: Bool
    let remove: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Agente", selection: $child.agentID) {
                ForEach(agents) { agent in Text("\(agent.name) · revisión \(agent.revision)").tag(agent.id) }
            }.onChange(of: child.agentID) { _, value in
                if let agent = agents.first(where: { $0.id == value }) { child.agentRevision = agent.revision }
            }
            TextEditor(text: $child.message).frame(minHeight: 70).border(.quaternary)
                .accessibilityLabel("Instrucciones de la tarea delegada")
            Stepper("Máximo \(child.maxSteps) pasos", value: $child.maxSteps, in: 1...32)
            if mayRemove { Button("Retirar tarea", role: .destructive, action: remove) }
        }.padding(10).background(.quaternary, in: .rect(cornerRadius: 8))
    }
}

private struct DelegatedChildView: View {
    @Bindable var store: ChatStore
    let child: DelegatedChildSnapshot
    @State private var correction = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Tarea \(child.index + 1) · \(store.management.agents.first(where: { $0.id == child.agentID })?.name ?? child.agentID)")
                .font(.headline)
            Text(DelegationPresentation.status(child.status)).font(.caption).foregroundStyle(.secondary)
            if child.unknownEffects { Text("Hay efectos sin confirmar. No deben repetirse automáticamente.").font(.caption).foregroundStyle(.orange) }
            if let answer = child.answer {
                Text("Reporte del modelo").font(.caption.weight(.semibold))
                Text(answer).font(.callout).textSelection(.enabled)
                if child.answerTruncated { Text("Respuesta abreviada en esta vista.").font(.caption).foregroundStyle(.secondary) }
            }
            if !child.receipts.isEmpty { ReceiptListView(receipts: child.receipts) }
            if child.receiptsTruncated { Text("Se muestran \(child.receipts.count) de \(child.receiptsTotal) recibos; consulta el detalle en la conversación hija.").font(.caption) }
            if child.settled {
                Button("Abrir conversación hija") { Task { await store.openDelegatedChild(child) } }.disabled(!store.canChangeSession)
            } else {
                TextField("Corrección para esta tarea", text: $correction, axis: .vertical).lineLimit(2...4)
                Button("Revisar corrección…") { Task { await store.steerDelegatedChild(child, message: correction) } }
                    .disabled(store.delegation.isChanging || !ManagementValidation.text(correction, limit: 8000))
            }
        }.padding(10).background(.background, in: .rect(cornerRadius: 8))
    }
}

struct DelegatedApprovalsView: View {
    @Bindable var coordination: DelegationStore
    var body: some View {
        if !coordination.approvals.isEmpty {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(coordination.approvals) { operation in
                        TimelineView(.periodic(from: .now, by: 1)) { timeline in
                            VStack(alignment: .leading, spacing: 8) {
                                Label("Aprobación de tarea delegada", systemImage: "hand.raised").font(.headline)
                                Text("Sesión \(operation.scope.sessionID.prefix(12)) · \(operation.toolName)").font(.caption)
                                ScrollView { Text(operation.argumentsJSON).font(.caption.monospaced()).textSelection(.enabled) }.frame(maxHeight: 110)
                                HStack {
                                    Button("Rechazar tarea") { Task { await coordination.decide(operation, allow: false) } }
                                    if !operation.isExpired(at: timeline.date) {
                                        Button("Permitir esta operación hija") { Task { await coordination.decide(operation, allow: true) } }
                                            .buttonStyle(.borderedProminent)
                                    } else { Text("Solicitud caducada").foregroundStyle(.orange) }
                                }.disabled(coordination.isChanging)
                            }.padding(12).background(.quaternary, in: .rect(cornerRadius: 10))
                        }
                    }
                }.padding(16)
            }.frame(maxHeight: 350)
        }
    }
}

enum DelegationPresentation {
    static func status(_ status: String) -> String {
        switch status {
        case "queued": return "En cola"
        case "running": return "En curso"
        case "waiting_approval": return "Esperando aprobación"
        case "cancelling": return "Cancelación solicitada"
        case "cleaning": return "Asentando la limpieza"
        case "completed": return "Completado"
        case "failed": return "No completado"
        case "cancelled": return "Cancelado"
        case "interrupted": return "Interrumpido; no se reanudará automáticamente"
        default: return "Sin confirmar"
        }
    }
}
