import DesktopCore
import SwiftUI

struct MemoryView: View {
    let store: ChatStore
    @Bindable var access: AccessStore
    @Binding var page: MemoryPanelPage
    private var semanticAvailable: Bool {
        guard let status = store.memoryIntelligence.status else { return false }
        return status.scope == store.selected?.scope && status.model.available && status.model.runtimeAvailable
    }
    private var maySearch: Bool {
        !access.isLoadingMemory && !access.memoryQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && access.memoryQuery.utf8.count <= 512 && (access.memoryQueryMode == .lexical || semanticAvailable)
    }

    init(store: ChatStore, page: Binding<MemoryPanelPage>) {
        self.store = store
        _access = Bindable(wrappedValue: store.access)
        _page = page
    }

    var body: some View {
        VStack(spacing: 0) {
            Picker("Panel de memoria", selection: $page) {
                Text("Notas").tag(MemoryPanelPage.notes)
                Text("Uso automático").tag(MemoryPanelPage.intelligence)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 20)
            .padding(.top, 14)
            if page == .notes { notes }
            else { MemoryIntelligenceView(store: store.memoryIntelligence) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task(id: store.memoryIntelligenceSelectionKey) {
            store.synchronizeMemoryIntelligenceContext()
            await store.memoryIntelligence.refresh()
            if !Task.isCancelled, page == .notes {
                await store.access.loadDocuments()
                if !Task.isCancelled { await store.memoryIntelligence.refresh() }
            }
        }
        .onChange(of: page) { _, selectedPage in
            store.synchronizeMemoryIntelligenceContext()
            if selectedPage == .notes {
                Task { await store.access.loadDocuments(); await store.memoryIntelligence.refresh() }
            }
        }
    }

    private var notes: some View {
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
                        await store.memoryIntelligence.refresh()
                    }
                }.disabled(store.access.isLoadingMemory || !store.access.memoryEnabled)
            }
            if !store.access.memoryEnabled {
                ContentUnavailableView("Memoria no disponible", systemImage: "tray",
                                       description: Text("El servicio no ha habilitado esta capacidad."))
            } else {
                Picker("Buscar por", selection: $access.memoryQueryMode) {
                    Text("Palabras").tag(MemoryRecallMode.lexical)
                    Text("Significado").tag(MemoryRecallMode.semanticLocal).disabled(!semanticAvailable)
                    Text("Combinada").tag(MemoryRecallMode.hybridLocal).disabled(!semanticAvailable)
                }
                .pickerStyle(.segmented)
                .disabled(access.isLoadingMemory)
                HStack {
                    TextField("Buscar en las notas aprobadas", text: $access.memoryQuery)
                        .onSubmit { if maySearch { Task { await store.access.searchMemory() } } }
                    Button("Buscar") { Task { await store.access.searchMemory() } }
                        .disabled(!maySearch)
                }
                if access.memoryQuery.utf8.count > 512 {
                    Text(MemoryManualSearchPresentation.queryTooLarge).font(.caption).foregroundStyle(.red)
                } else if !semanticAvailable {
                    Text("Para buscar por significado, prepara el modelo local en Uso automático. No hace falta activar la recuperación automática.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Esta búsqueda es local y no activa el envío automático de contexto.").font(.caption).foregroundStyle(.secondary)
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
                            if store.access.matches.isEmpty && !store.access.isLoadingMemory && access.memorySearchHasResponse {
                                Text("No hay coincidencias para esta búsqueda.").foregroundStyle(.secondary)
                            }
                            if access.memorySearchHasMore {
                                Text("Hay más coincidencias. Afina la consulta para acotar los resultados.").font(.caption).foregroundStyle(.secondary)
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
        .padding(20)
    }
}
