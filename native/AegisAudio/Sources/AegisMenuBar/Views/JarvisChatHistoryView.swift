import AegisAudioCore
import SwiftUI

struct JarvisChatHistoryView: View {
    let model: MenuBarModel
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Historial").font(.system(size: 14, weight: .semibold))
                Spacer()
                if !chat.showTrash {
                    Button {
                        if let entry = chat.conversations.first(where: { $0.id == chat.conversationID }) {
                            Task { await chat.setTrashed(entry, model: model, trashed: true) }
                        }
                    } label: { Image(systemName: "trash").frame(width: 16, height: 16) }
                        .buttonStyle(MenuBarSmallButtonStyle()).help("Mover chat a la papelera")
                        .accessibilityLabel("Mover chat a la papelera")
                        .disabled(!model.canManageChatHistory
                                  || !chat.conversations.contains(where: { $0.id == chat.conversationID }))
                }
                Button { Task { await chat.refreshHistory(model: model) } } label: {
                    Image(systemName: "arrow.clockwise").frame(height: 16)
                }.buttonStyle(MenuBarSmallButtonStyle()).disabled(chat.historyLoading)
                    .help("Actualizar historial").accessibilityLabel("Actualizar historial")
            }
            Picker("Conversaciones", selection: Binding(get: { chat.showTrash }, set: { chat.showTrash = $0 })) {
                Text("Chats").tag(false)
                Text("Papelera").tag(true)
            }.pickerStyle(.segmented).labelsHidden()
                .accessibilityLabel("Conversaciones")
            TextField("Buscar por título", text: Binding(get: { chat.historyQuery },
                                                        set: { chat.historyQuery = String($0.prefix(128)) }))
                .textFieldStyle(.roundedBorder).font(.system(size: 12))
                .accessibilityLabel("Buscar conversaciones")
            List(selection: Binding<UUID?>(get: { chat.showTrash ? nil : chat.conversationID }, set: { id in
                guard let id, let entry = chat.conversations.first(where: { $0.id == id }), !chat.showTrash else { return }
                Task { await chat.open(entry, model: model) }
            })) {
                if chat.conversations.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        if chat.historyLoading { ProgressView().controlSize(.small) }
                        Text(chat.historyLoading ? "Cargando…" : !chat.historyQuery.isEmpty ? "Sin resultados para esta búsqueda." : chat.showTrash ? "La papelera está vacía." : "No hay conversaciones.")
                            .font(.system(size: 12)).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }.padding(.vertical, 12)
                }
                ForEach(chat.conversations) { entry in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: chat.showTrash ? "trash" : "bubble.left")
                            .foregroundStyle(.secondary).frame(width: 16)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(verbatim: entry.title).font(.system(size: 12, weight: .medium)).lineLimit(2)
                            Text(date(entry.updatedAt)).font(.system(size: 10)).foregroundStyle(.secondary)
                        }
                        if chat.showTrash {
                            Spacer(minLength: 0)
                            Button { Task { await chat.setTrashed(entry, model: model, trashed: false) } } label: {
                                Image(systemName: "arrow.uturn.backward")
                            }.buttonStyle(.borderless).help("Restaurar conversación")
                                .accessibilityLabel("Restaurar \(entry.title)")
                                .disabled(!model.canManageChatHistory)
                        }
                    }.padding(.vertical, 4).tag(entry.id).help(entry.title)
                        .contextMenu {
                            Button(chat.showTrash ? "Restaurar conversación" : "Mover a la papelera",
                                   systemImage: chat.showTrash ? "arrow.uturn.backward" : "trash") {
                                Task { await chat.setTrashed(entry, model: model, trashed: !entry.archived) }
                            }.disabled(!model.canManageChatHistory)
                        }
                }
                if chat.nextOffset != nil {
                    Button("Cargar más conversaciones") { Task { await chat.refreshHistory(model: model, append: true) } }
                        .font(.system(size: 11)).disabled(chat.historyLoading)
                }
            }.listStyle(.sidebar).scrollContentBackground(.hidden)
                .disabled(!model.canManageChatHistory)
            Text(chat.showTrash ? "Puedes restaurar los chats de la papelera." : "Guardado en este Mac")
                .font(.system(size: 10)).foregroundStyle(.secondary)
        }
        .padding(16)
        .task(id: "\(chat.historyQuery)|\(chat.showTrash)") {
            do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            await chat.refreshHistory(model: model)
        }
    }

    private func date(_ raw: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        return date?.formatted(.dateTime.day().month(.abbreviated).hour().minute()) ?? ""
    }
}
