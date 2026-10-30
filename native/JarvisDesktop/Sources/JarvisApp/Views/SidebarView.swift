import SwiftUI

struct SidebarView: View {
    @Bindable var store: ChatStore
    var onManage: () -> Void = {}
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
                .disabled(!store.canNavigateSession)
            }.padding(16)
            Picker("Agente para chats nuevos", selection: $store.newAgentID) {
                ForEach(store.management.agents) { agent in Text(agent.name).tag(agent.id) }
            }.padding(.horizontal, 14).padding(.bottom, 10).disabled(!store.canNavigateSession)
            List(selection: Binding(get: { store.selectedID }, set: { id in
                if let id { Task { await store.selectSession(id) } }
            })) {
                if !pinnedSessions.isEmpty {
                    Section("Fijadas") { ForEach(pinnedSessions) { session in row(session) } }
                }
                ForEach(Array(groupedSessions.keys).sorted(), id: \.self) { group in
                    Section(groupTitle(group)) {
                        ForEach(groupedSessions[group] ?? []) { session in row(session) }
                    }
                }
                if showArchived {
                    Section("Archivadas") {
                        ForEach(store.sessions.filter(\.archived)) { session in row(session) }
                    }
                }
            }
            .listStyle(.sidebar)
            .disabled(!store.canNavigateSession)
            Divider()
            if store.sessions.contains(where: \.archived) {
                Toggle("Mostrar archivadas", isOn: $showArchived)
                    .toggleStyle(.checkbox).font(.caption).padding(.horizontal, 14).padding(.top, 10)
            }
            Button("Chat temporal", systemImage: "clock.badge.questionmark") {
                Task { await store.createSession(temporary: true) }
            }
            .disabled(!store.canNavigateSession)
            .padding(14)
            Button("Carpetas y colaboración", systemImage: "folder") { onManage() }
                .disabled(!store.ready).font(.callout).padding(.bottom, 12)
            Text("Base independiente · Fase A")
                .font(.caption).foregroundStyle(.secondary).padding(.bottom, 12)
        }
        .sheet(item: $renaming) { session in
            RenameSessionView(session: session, store: store)
        }
        .onChange(of: store.management.agents.map(\.id)) { _, identities in
            if !identities.contains(store.newAgentID), identities.contains("jarvis") { store.newAgentID = "jarvis" }
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
        VStack(alignment: .leading, spacing: 3) {
            Label(session.title, systemImage: session.archived ? "archivebox" : session.parentSessionID != nil ? "arrowshape.turn.up.right" : session.temporary ? "clock.badge.questionmark" : "bubble.left")
                .lineLimit(1)
            if store.isSessionRunning(session.scope) {
                Text(store.runTracker.observations[session.scope]?.acceptanceUnconfirmed == true ? "Envío sin confirmar"
                     : store.runTracker.observations[session.scope]?.phase == "waiting_approval"
                     ? "Aprobación pendiente" : "En curso")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.tag(session.id)
            .contextMenu {
                Button("Abrir organización…") { Task { await store.selectSession(session.id); onManage() } }
                    .disabled(!store.canNavigateSession)
                Button("Renombrar…") { renaming = session }.disabled(!mayMutate(session))
                Button("Ramificar conversación…") { Task { await store.forkSession(session) } }.disabled(!mayMutate(session))
                Button(session.archived ? "Restaurar" : "Archivar") {
                    if !session.archived { showArchived = true }
                    Task { await store.setArchived(session, archived: !session.archived) }
                }.disabled(!mayMutate(session))
                Divider()
                Button("Borrar…", role: .destructive) { deleting = session }.disabled(!mayMutate(session))
            }
    }

    private var pinnedSessions: [ChatSession] {
        store.sessions.filter { !$0.archived && store.sidebarBindings[$0.scope]?.pinned == true }
    }
    private var groupedSessions: [String: [ChatSession]] {
        Dictionary(grouping: store.sessions.filter { !$0.archived && store.sidebarBindings[$0.scope]?.pinned != true }) { session in
            guard let binding = store.sidebarBindings[session.scope] else { return "unbound" }
            return binding.workspaceID + "\u{0}" + (binding.group ?? "")
        }
    }
    private func groupTitle(_ key: String) -> String {
        let parts = key.split(separator: "\u{0}", omittingEmptySubsequences: false)
        guard parts.count == 2 else { return "Conversaciones" }
        let workspace = store.sidebarWorkspaces[String(parts[0])] ?? "Carpeta de trabajo"
        return parts[1].isEmpty ? workspace : workspace + " · " + String(parts[1])
    }
    private func mayMutate(_ session: ChatSession) -> Bool {
        store.canChangeSession && !store.isSessionRunning(session.scope)
    }
}
