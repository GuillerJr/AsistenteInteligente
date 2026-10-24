import SwiftUI

struct SidebarView: View {
    @Bindable var store: ChatStore
    @State private var showArchived = false
    @State private var renaming: ChatSession?
    @State private var deleting: ChatSession?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Jarvis").font(.title2.bold())
                Spacer()
                Button {
                    Task { await store.createSession(temporary: false) }
                } label: { Image(systemName: "square.and.pencil") }
                .help("Nueva conversación (⌘N)")
                .accessibilityLabel("Nueva conversación")
                .disabled(!store.canChangeSession)
            }.padding(16)
            List(selection: Binding(get: { store.selectedID }, set: { id in
                if let id { Task { await store.selectSession(id) } }
            })) {
                Section("Conversaciones") {
                    ForEach(store.sessions.filter { !$0.archived }) { session in row(session) }
                }
                if showArchived {
                    Section("Archivadas") {
                        ForEach(store.sessions.filter(\.archived)) { session in row(session) }
                    }
                }
            }
            .listStyle(.sidebar)
            .disabled(!store.canChangeSession)
            Divider()
            if store.sessions.contains(where: \.archived) {
                Toggle("Mostrar archivadas", isOn: $showArchived)
                    .toggleStyle(.checkbox).font(.caption).padding(.horizontal, 14).padding(.top, 10)
            }
            Button("Chat temporal", systemImage: "clock.badge.questionmark") {
                Task { await store.createSession(temporary: true) }
            }
            .disabled(!store.canChangeSession)
            .padding(14)
            Text("Base independiente · Fase A")
                .font(.caption).foregroundStyle(.secondary).padding(.bottom, 12)
        }
        .sheet(item: $renaming) { session in
            RenameSessionView(session: session, store: store)
        }
        .confirmationDialog("¿Borrar esta conversación?", isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }
        ), presenting: deleting) { session in
            Button("Borrar conversación", role: .destructive) { Task { await store.deleteSession(session) } }
            Button("Cancelar", role: .cancel) {}
        } message: { session in
            Text("Se borrarán el registro local de «\(session.title)», sus mensajes y sus recibos en Jarvis. Esta acción no se puede deshacer.")
        }
    }

    private func row(_ session: ChatSession) -> some View {
        Label(session.title, systemImage: session.archived ? "archivebox" : session.temporary ? "clock.badge.questionmark" : "bubble.left")
            .lineLimit(1).tag(session.id)
            .contextMenu {
                Button("Renombrar…") { renaming = session }
                Button(session.archived ? "Restaurar" : "Archivar") {
                    if !session.archived { showArchived = true }
                    Task { await store.setArchived(session, archived: !session.archived) }
                }
                Divider()
                Button("Borrar…", role: .destructive) { deleting = session }
            }
    }
}
