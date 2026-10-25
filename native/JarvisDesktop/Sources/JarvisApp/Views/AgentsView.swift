import DesktopCore
import SwiftUI

struct AgentsView: View {
    @Bindable var store: ChatStore
    @State private var editing: AgentDescriptor?
    @State private var showEditor = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Agentes").font(.title2.weight(.semibold))
                Text("Cada conversación conserva su agente. Las instrucciones y skills cambian su contexto, pero no conceden permisos.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Nuevo agente…") { editing = nil; showEditor = true }
                        .disabled(!store.canChangeSession)
                    Button("Actualizar") { Task { await store.management.refreshFromUI() } }
                }
                ForEach(store.management.agents) { agent in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(agent.name).font(.headline)
                        Text(agent.id).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        DisclosureGroup("Instrucciones · versión \(agent.revision)") {
                            Text(agent.instructions.isEmpty ? "Sin instrucciones adicionales." : agent.instructions).textSelection(.enabled)
                        }
                        HStack {
                            Button("Editar…") { editing = agent; showEditor = true }
                            if agent.id != "jarvis" {
                                Button("Borrar…", role: .destructive) { Task { await store.management.deleteAgent(agent) } }
                            }
                        }.disabled(!store.canChangeSession)
                    }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
                }
                if let error = store.management.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            }.padding(20)
        }
        .task { await store.management.refreshFromUI() }
        .sheet(isPresented: $showEditor) { AgentEditorView(agent: editing, store: store) }
    }
}

private struct AgentEditorView: View {
    let agent: AgentDescriptor?
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var instructions: String

    init(agent: AgentDescriptor?, store: ChatStore) {
        self.agent = agent; self.store = store
        _name = State(initialValue: agent?.name ?? "")
        _instructions = State(initialValue: agent?.instructions ?? "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(agent == nil ? "Nuevo agente" : "Editar agente").font(.title2.weight(.semibold))
            TextField("Nombre", text: $name)
            Text("Instrucciones adicionales").font(.headline)
            TextEditor(text: $instructions).font(.system(.body, design: .monospaced)).frame(height: 220).border(.quaternary)
                .accessibilityLabel("Instrucciones adicionales del agente")
            Text("Se usa la conexión elegida de Codex. Las instrucciones no sustituyen las aprobaciones de Jarvis.").font(.caption).foregroundStyle(.secondary)
            if let error = store.management.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Revisar y guardar…") {
                    Task { if await store.management.saveAgent(agent, name: name, instructions: instructions) { dismiss() } }
                }.disabled(!store.canChangeSession || !ManagementValidation.text(name, limit: 256) || instructions.utf8.count > 8192)
            }
        }.padding(24).frame(width: 620)
    }
}
