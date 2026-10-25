import DesktopCore
import SwiftUI

struct ProcessesView: View {
    @Bindable var store: ChatStore
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Procesos de esta conversación").font(.title2.weight(.semibold))
                Button("Actualizar") { Task { await store.management.loadProcesses() } }
                if let catalog = store.management.processCatalog {
                    Label(catalog.available ? "Aislamiento disponible" : "Aislamiento no disponible",
                          systemImage: catalog.available ? "shield.lefthalf.filled" : "exclamationmark.shield")
                    if let backend = catalog.backend { Text("Backend: \(backend)").font(.caption).foregroundStyle(.secondary) }
                    Text("La red está denegada. Sólo se muestran procesos del ámbito seleccionado. Un código de salida no verifica los cambios que el programa afirme realizar.")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(catalog.processes) { listed in
                        let process = store.management.polled[listed.id] ?? listed
                        VStack(alignment: .leading, spacing: 10) {
                            Text(listed.running ? "En ejecución" : "Finalizado · \(ManagementPresentation.state(listed.state))").font(.headline)
                            Text(process.id).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                            if let exit = listed.exitCode { Text("Código de salida: \(exit)").font(.caption) }
                            Text("Salida: \(listed.outputBytes) bytes · \(listed.truncated ? "vista truncada" : "retenida")")
                                .font(.caption).foregroundStyle(.secondary)
                            Text(store.management.processOutput[process.id] ?? process.output)
                                .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                            DisclosureGroup("Hash del flujo de salida") { Text(listed.hash).font(.system(.caption2, design: .monospaced)).textSelection(.enabled) }
                            HStack {
                                Button("Releer salida") { Task { await store.management.poll(process, more: false) } }
                                if process.nextCursor < listed.retainedBytes { Button("Leer más") { Task { await store.management.poll(process, more: true) } } }
                            }
                            if listed.running {
                                Button("Solicitar detención en el chat") { store.prepareProcessStop(listed) }
                                    .disabled(!store.canChangeSession || !store.draft.isEmpty)
                            }
                        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
                    }
                    if catalog.processes.isEmpty { Text("No hay procesos registrados en esta conversación.").foregroundStyle(.secondary) }
                }
                Text("Para ejecutar, escribir en stdin o detener un proceso, envía una solicitud en el chat y revisa su tarjeta. Esta vista no evita la autoridad de aprobación.")
                    .font(.caption).foregroundStyle(.secondary)
                if let error = store.management.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            }.padding(20)
        }
        .task { await store.management.loadProcesses() }
        .onChange(of: store.selectedID) { _, _ in Task { await store.management.loadProcesses() } }
    }
}
