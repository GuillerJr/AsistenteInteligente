import SwiftUI

struct RenameSessionView: View {
    let session: ChatSession
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    @State private var title: String
    @State private var saving = false

    init(session: ChatSession, store: ChatStore) {
        self.session = session; self.store = store
        _title = State(initialValue: session.title)
    }

    private var valid: Bool {
        !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && title.utf8.count <= 512
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Renombrar conversación").font(.headline)
            TextField("Nombre", text: $title).textFieldStyle(.roundedBorder)
                .onSubmit { if valid && !saving { save() } }
            if let error = store.errorMessage { Text(error).font(.caption).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Guardar") { save() }.disabled(!valid || saving)
                    .keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 420)
    }

    private func save() {
        saving = true
        Task {
            if await store.renameSession(session, title: title) { dismiss() }
            saving = false
        }
    }
}
