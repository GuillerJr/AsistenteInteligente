import DesktopCore
import SwiftUI

struct CapabilitiesView: View {
    @Bindable var store: ChatStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Herramientas de la base").font(.title2.weight(.semibold))
                VStack(alignment: .leading, spacing: 10) {
                    Label(store.access.workspacePrivateDefault ? "Espacio privado de Jarvis" : "Carpeta de trabajo", systemImage: "folder")
                        .font(.headline)
                    Text(store.access.workspacePath ?? "No hay una carpeta elegida")
                        .textSelection(.enabled).foregroundStyle(.secondary)
                    Button("Elegir carpeta…") { Task { await store.access.chooseWorkspace() } }
                        .disabled(!store.canChangeSession || store.access.isChangingWorkspace)
                    if !store.access.workspaceAvailable {
                        Text(store.access.workspaceError ?? "Esta carpeta no está disponible. Elige una carpeta para continuar.")
                            .font(.caption).foregroundStyle(.red)
                    }
                }
                Divider()
                if store.access.tools.isEmpty {
                    Text("El servicio no ha publicado herramientas disponibles.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(store.access.tools) { tool in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(tool.name).font(.headline)
                                if tool.requiresConfirmation {
                                    Label("Requiere aprobación", systemImage: "hand.raised")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Text(tool.description).foregroundStyle(.secondary)
                        }
                    }
                }
                if store.selected != nil && store.access.toolsEnabled {
                    Divider()
                    Button("Revocar autorizaciones de esta conversación", role: .destructive) {
                        Task { await store.access.revokePending() }
                    }
                    Text("La revocación afecta las operaciones que todavía no se han despachado.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let error = store.access.errorMessage {
                    Text(error).foregroundStyle(.red).textSelection(.enabled)
                }
            }.padding(24)
        }.frame(minWidth: 380, idealWidth: 440)
    }
}
