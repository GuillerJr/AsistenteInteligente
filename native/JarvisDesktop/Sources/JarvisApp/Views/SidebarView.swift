import SwiftUI

struct SidebarView: View {
    @Bindable var store: ChatStore

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
                    ForEach(store.sessions.filter { !$0.archived }) { session in
                        Label(session.title, systemImage: session.temporary ? "clock.badge.questionmark" : "bubble.left")
                            .lineLimit(1).tag(session.id)
                    }
                }
            }
            .listStyle(.sidebar)
            .disabled(!store.canChangeSession)
            Divider()
            Button("Chat temporal", systemImage: "clock.badge.questionmark") {
                Task { await store.createSession(temporary: true) }
            }
            .disabled(!store.canChangeSession)
            .padding(14)
            Text("Base independiente · Fase A")
                .font(.caption).foregroundStyle(.secondary).padding(.bottom, 12)
        }
    }
}
