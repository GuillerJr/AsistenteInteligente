import SwiftUI

struct MemoryView: View {
    let store: ChatStore
    @Bindable var access: AccessStore

    init(store: ChatStore) {
        self.store = store
        _access = Bindable(wrappedValue: store.access)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(store.selected?.temporary == true ? "Memoria temporal de esta conversación"
                     : store.access.workspacePrivateDefault ? "Memoria del espacio privado de Jarvis" : "Memoria de esta carpeta")
                .font(.title3.weight(.semibold))
                Spacer()
                Button("Actualizar") {
                    Task {
                        if store.access.memoryQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            await store.access.loadDocuments()
                        } else { await store.access.searchMemory() }
                    }
                }.disabled(store.access.isLoadingMemory || !store.access.memoryEnabled)
            }
            if !store.access.memoryEnabled {
                ContentUnavailableView("Memoria no disponible", systemImage: "tray",
                                       description: Text("El servicio no ha habilitado esta capacidad."))
            } else {
                HStack {
                    TextField("Buscar texto en la memoria", text: $access.memoryQuery)
                        .onSubmit { Task { await store.access.searchMemory() } }
                    Button("Buscar") { Task { await store.access.searchMemory() } }
                        .disabled(store.access.isLoadingMemory || store.access.memoryQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if store.access.isLoadingMemory { ProgressView().controlSize(.small) }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        if !store.access.memoryQuery.isEmpty {
                            ForEach(store.access.matches) { match in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(match.document.title.isEmpty ? match.document.key : match.document.title)
                                        .font(.headline)
                                    Text(match.excerpt).textSelection(.enabled)
                                    Text(match.document.sourceURI).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                    HStack {
                                        Text("Versión \(match.document.revision)").font(.caption2).foregroundStyle(.secondary)
                                        Spacer()
                                        if store.access.verifiedCitations.contains(match.id) {
                                            Label("Coincide con la memoria actual", systemImage: "checkmark.seal").font(.caption)
                                        } else {
                                            Button("Comprobar cita") { Task { await store.access.verifyCitation(match) } }
                                                .font(.caption)
                                        }
                                    }
                                }.padding(12).background(.quaternary, in: .rect(cornerRadius: 10))
                            }
                            if store.access.matches.isEmpty && !store.access.isLoadingMemory {
                                Text("No hay coincidencias para esta búsqueda.").foregroundStyle(.secondary)
                            }
                        } else {
                            ForEach(store.access.documents) { document in
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(document.title.isEmpty ? document.key : document.title).font(.headline)
                                    Text(document.sourceURI).font(.caption).foregroundStyle(.secondary)
                                    Text("Versión \(document.revision)").font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                            if store.access.hasMoreDocuments {
                                Button("Cargar más") { Task { await store.access.loadDocuments(reset: false) } }
                                    .disabled(store.access.isLoadingMemory)
                            }
                            if store.access.documents.isEmpty && !store.access.isLoadingMemory {
                                Text("Todavía no hay documentos en este ámbito.").foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if let error = store.access.errorMessage {
                    Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled)
                }
            }
        }
        .padding(20).frame(minWidth: 380, idealWidth: 440)
        .task { await store.access.loadDocuments() }
        .onChange(of: store.selectedID) { _, _ in Task { await store.access.loadDocuments() } }
        .onChange(of: store.access.workspacePath) { _, _ in Task { await store.access.loadDocuments() } }
    }
}
