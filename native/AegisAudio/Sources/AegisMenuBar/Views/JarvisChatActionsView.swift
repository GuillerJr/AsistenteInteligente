import AegisAudioCore
import SwiftUI

struct JarvisChatActionsView: View {
    let entry: ChatConversationSummary
    let model: MenuBarModel
    let rename: () -> Void

    var body: some View {
        if !entry.archived {
            Button("Renombrar chat", systemImage: "pencil", action: rename)
            Button("Crear fork", systemImage: "arrow.triangle.branch") {
                Task { await model.chat.fork(entry, model: model) }
            }.help("Continuar el historial en una conversación independiente")
            Button("Continuar en Telegram", systemImage: "paperplane") {
                Task { await model.chat.continueInTelegram(entry, model: model) }
            }.help("Seleccionar este chat para los siguientes mensajes del móvil")
            Divider()
        }
        Button(entry.archived ? "Restaurar chat" : "Mover a la papelera",
               systemImage: entry.archived ? "arrow.uturn.backward" : "trash") {
            Task { await model.chat.setTrashed(entry, model: model, trashed: !entry.archived) }
        }
    }
}

struct JarvisRenameChatView: View {
    let entry: ChatConversationSummary
    let model: MenuBarModel
    @State private var title: String
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool

    init(entry: ChatConversationSummary, model: MenuBarModel) {
        self.entry = entry
        self.model = model
        _title = State(initialValue: entry.title)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Label("Renombrar chat", systemImage: "pencil")
                .font(.system(size: 19, weight: .semibold))
            TextField("Nombre de la conversación", text: $title)
                .focused($focused).jarvisInput(focused: focused)
                .onChange(of: title) { _, value in title = String(value.prefix(128)) }
                .accessibilityLabel("Nombre de la conversación")
            HStack {
                Text("Hasta 128 caracteres").font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Guardar") {
                    let name = title
                    dismiss()
                    Task { await model.chat.rename(entry, title: name, model: model) }
                }.buttonStyle(JarvisButtonStyle(prominent: true, compact: true))
                    .keyboardShortcut(.defaultAction)
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              || !model.canManageChatHistory)
            }
        }.padding(24).frame(width: 440).jarvisSheet()
            .onAppear { focused = true }
    }
}
