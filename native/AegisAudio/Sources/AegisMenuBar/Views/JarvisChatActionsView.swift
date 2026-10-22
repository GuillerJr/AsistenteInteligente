import AegisAudioCore
import SwiftUI

struct JarvisChatActionsView: View {
    let entry: ChatConversationSummary
    let model: MenuBarModel
    let rename: () -> Void

    var body: some View {
        if !entry.archived {
            Button(String(localized: "Renombrar chat", bundle: .module), systemImage: "pencil", action: rename)
            Button(entry.filed ? String(localized: "Desarchivar chat", bundle: .module) : String(localized: "Archivar chat", bundle: .module), systemImage: "archivebox") {
                Task { await model.chat.setFiled(entry, model: model, filed: !entry.filed) }
            }
            Button(String(localized: "Crear fork", bundle: .module), systemImage: "arrow.triangle.branch") {
                Task { await model.chat.fork(entry, model: model) }
            }.help(String(localized: "Continuar el historial en una conversación independiente", bundle: .module))
            Button(String(localized: "Continuar en Telegram", bundle: .module), systemImage: "paperplane") {
                Task { await model.chat.continueInTelegram(entry, model: model) }
            }.help(String(localized: "Seleccionar este chat para los siguientes mensajes del móvil", bundle: .module))
            if !model.chat.projects.isEmpty || entry.projectID != nil {
                Menu(String(localized: "Mover a proyecto", bundle: .module), systemImage: "folder") {
                    ForEach(model.chat.projects) { project in
                        Button(project.name) { move(to: project.project_id) }
                            .disabled(entry.projectID?.uuidString.lowercased() == project.project_id)
                    }
                    if entry.projectID != nil {
                        Divider()
                        Button(String(localized: "Quitar del proyecto", bundle: .module)) { move(to: nil) }
                    }
                }
            }
            Divider()
        }
        Button(entry.archived ? String(localized: "Restaurar chat", bundle: .module) : String(localized: "Mover a la papelera", bundle: .module),
               systemImage: entry.archived ? "arrow.uturn.backward" : "trash") {
            Task { await model.chat.setTrashed(entry, model: model, trashed: !entry.archived) }
        }
    }
}

extension JarvisChatActionsView {
    private func move(to project: String?) {
        Task {
            var payload: [String: Any] = ["conversation_id": entry.id.uuidString.lowercased()]
            if let project { payload["project_id"] = project }
            await model.chat.projectCall("projects.assign", payload, model: model)
            await model.chat.refreshHistory(model: model)
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
            Label(String(localized: "Renombrar chat", bundle: .module), systemImage: "pencil")
                .font(.system(size: 19, weight: .semibold))
            TextField(String(localized: "Nombre de la conversación", bundle: .module), text: $title)
                .focused($focused).jarvisInput(focused: focused)
                .onChange(of: title) { _, value in title = String(value.prefix(128)) }
                .accessibilityLabel(String(localized: "Nombre de la conversación", bundle: .module))
            HStack {
                Text(String(localized: "Hasta 128 caracteres", bundle: .module)).font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Button(String(localized: "Cancelar", bundle: .module)) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(String(localized: "Guardar", bundle: .module)) {
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
