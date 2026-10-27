import SwiftUI

struct RunInputView: View {
    @Bindable var store: ChatStore
    @State private var message = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Turno y cola").font(.title3.bold())
                Text("Una corrección se dirige al turno activo. Un mensaje en cola espera el recibo y la limpieza de la ejecución anterior; un efecto sin confirmar detiene su continuación.")
                    .font(.callout).foregroundStyle(.secondary)
                if let run = store.activeRunID { Text(run).font(.caption.monospaced()).textSelection(.enabled) }
                TextEditor(text: $message).frame(minHeight: 120).border(.quaternary)
                    .accessibilityLabel("Mensaje para corregir el turno o poner en cola")
                Button("Revisar corrección del turno…") { Task { await store.sendRunInput(message, queued: false) } }
                    .disabled(!store.canSupplyRunInput || store.runPhase != "running" || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || message.utf8.count > 8000)
                Button("Revisar mensaje en cola…") { Task { await store.sendRunInput(message, queued: true) } }
                    .disabled(!store.canSupplyRunInput || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || message.utf8.count > 32000)
                if let notice = store.runInputNotice { Text(notice).font(.callout).textSelection(.enabled) }
                if store.hasUnconfirmedQueue {
                    Button("Consultar envío en cola pendiente") { Task { await store.inspectQueuedInput() } }
                        .disabled(!store.ready || store.busy)
                }
            }.padding(20)
        }
    }
}
