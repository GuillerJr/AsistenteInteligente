import SwiftUI

struct ComposerView: View {
    @Bindable var store: ChatStore
    @FocusState private var focused: Bool

    private var phaseLabel: String {
        switch store.runPhase {
        case "queued": return "En cola"
        case "running": return "Codex está respondiendo…"
        case "waiting_approval": return "Esperando tu aprobación"
        case "cancelling": return "Cancelación solicitada…"
        case "cleaning": return "Terminando la limpieza…"
        case "disconnected": return "Estado pendiente: conexión interrumpida"
        case "checking": return "Consultando el estado de la ejecución…"
        case "completed": return "Completado"
        case "failed": return "No se pudo completar"
        case "cancelled": return "Cancelado y asentado"
        case "interrupted": return "Interrumpido"
        default: return ""
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !phaseLabel.isEmpty {
                HStack {
                    if store.activeRunID != nil && store.runPhase != "disconnected" {
                        ProgressView().controlSize(.small)
                    }
                    Text(phaseLabel).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if store.runPhase == "disconnected" {
                        Button("Consultar estado") { store.resumeStatus() }
                    }
                }
            }
            VStack(spacing: 10) {
                TextField("Escribe un mensaje…", text: $store.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(2...7).focused($focused)
                    .onSubmit { if store.canSend { Task { await store.send() } } }
                    .disabled(!store.ready || store.activeRunID != nil)
                HStack {
                    Text(store.selected?.temporary == true ? "Chat temporal" : "Codex · ChatGPT")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if store.activeRunID != nil {
                        Button("Cancelar", systemImage: "stop.fill") { Task { await store.cancel() } }
                            .disabled(["cancelling", "cleaning"].contains(store.runPhase))
                    } else {
                        Button("Enviar", systemImage: "arrow.up") { Task { await store.send() } }
                            .buttonStyle(.borderedProminent).disabled(!store.canSend)
                            .keyboardShortcut(.return, modifiers: [.command])
                    }
                }
            }
            .padding(16).background(.quaternary, in: .rect(cornerRadius: 16))
            Text(store.access.toolsEnabled ? "Herramientas disponibles · Las operaciones que cambian datos requieren tu aprobación"
                 : "Herramientas de la base pendientes de construcción")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .onAppear { focused = true }
    }
}
