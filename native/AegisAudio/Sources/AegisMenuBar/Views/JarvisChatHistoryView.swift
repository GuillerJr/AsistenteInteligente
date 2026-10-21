import AegisAudioCore
import SwiftUI

struct JarvisChatHistoryView: View {
    let model: MenuBarModel
    @State private var renameEntry: ChatConversationSummary?
    @State private var confirmingEmptyTrash = false
    @FocusState private var searchFocused: Bool
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            controls.padding(16)
            Divider()
            if !chat.attention.isEmpty && !chat.showTrash { attentionSection; Divider() }
            if chat.conversations.isEmpty { emptyState }
            else { conversations }
            Divider()
            VStack(alignment: .leading, spacing: 12) {
                if chat.showTrash {
                    Button(role: .destructive) { confirmingEmptyTrash = true } label: {
                        Label("Vaciar papelera", systemImage: "trash").frame(maxWidth: .infinity)
                    }.buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                }
                Label(chat.showTrash ? "Restaura un chat para continuar." : "Historial cifrado en este Mac",
                      systemImage: chat.showTrash ? "arrow.uturn.backward" : "lock.shield")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }.padding(16)
        }
        .sheet(item: $renameEntry) { JarvisRenameChatView(entry: $0, model: model) }
        .confirmationDialog("¿Vaciar toda la papelera?", isPresented: $confirmingEmptyTrash,
                            titleVisibility: .visible) {
            Button("Eliminar todos los chats de la papelera", role: .destructive) {
                Task { await chat.emptyTrash(model: model) }
            }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Se eliminarán permanentemente todos los chats de la papelera y sus documentos, incluidos los que no muestra la búsqueda. Esta acción no se puede deshacer.")
        }
        .task {
            // Tasks and goals change on their own schedule; check every minute.
            while !Task.isCancelled {
                await chat.refreshAttention(model: model)
                do { try await Task.sleep(for: .seconds(60)) } catch { return }
            }
        }
        .task(id: "\(chat.historyQuery)|\(chat.showTrash)") {
            do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            await chat.refreshHistory(model: model)
        }
    }

    private var attentionSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Requiere tu atención", systemImage: "exclamationmark.bubble")
                .font(.system(size: 12, weight: .semibold)).foregroundStyle(.orange)
            ForEach(chat.attention) { entry in
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.title).font(.system(size: 12, weight: .semibold)).lineLimit(1)
                    Text(entry.reason).font(.system(size: 11)).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        if entry.conversationID != nil {
                            Button("Abrir") { Task { await chat.openAttention(entry, model: model) } }
                        }
                        if entry.action != "review_approval" {
                            Button(entry.action == "run_task" ? "Reintentar" : "Reanudar") {
                                Task { await chat.resolveAttention(entry, model: model) }
                            }
                        }
                        Button("Posponer") {
                            Task { await chat.refreshAttention(model: model, snoozing: entry.id) }
                        }.help("Ocultarlo durante una hora")
                    }
                    .buttonStyle(MenuBarSmallButtonStyle()).font(.system(size: 11))
                    .disabled(!model.canManageChatHistory)
                }
                .padding(10).voiceEnrollmentCard()
            }
        }.padding(16)
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Conversaciones").font(.system(size: 14, weight: .semibold))
                Spacer()
                MenuBarIconButton(symbol: "square.and.pencil", title: "Nuevo chat") {
                    chat.newConversation()
                    chat.showTrash = false
                }.disabled(!model.canManageChatHistory)
                MenuBarIconButton(symbol: "arrow.clockwise", title: "Actualizar historial") {
                    Task { await chat.refreshHistory(model: model) }
                }.disabled(chat.historyLoading || !model.canReadChatHistory)
            }
            JarvisSegmentedPicker(title: "Conversaciones",
                                  selection: Binding(get: { chat.showTrash }, set: { chat.showTrash = $0 }),
                                  options: [.init(value: false, title: "Chats"),
                                            .init(value: true, title: "Papelera")])
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Buscar chats", text: Binding(get: { chat.historyQuery },
                                                       set: { chat.historyQuery = String($0.prefix(128)) }))
                    .textFieldStyle(.plain).focused($searchFocused).accessibilityLabel("Buscar conversaciones")
                if !chat.historyQuery.isEmpty {
                    Button { chat.historyQuery = "" } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(.secondary).accessibilityLabel("Limpiar búsqueda")
                }
            }.jarvisInput(focused: searchFocused)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            if chat.historyLoading { ProgressView().controlSize(.small) }
            else { Image(systemName: !chat.historyQuery.isEmpty ? "magnifyingglass" : chat.showTrash ? "trash" : "bubble.left.and.bubble.right")
                    .font(.system(size: 25)).foregroundStyle(.tertiary) }
            Text(chat.historyLoading ? "Cargando historial…" : !chat.historyQuery.isEmpty ? "Sin coincidencias" : chat.showTrash ? "Papelera vacía" : "Tu primera conversación")
                .font(.system(size: 13, weight: .medium))
            Text(!chat.historyQuery.isEmpty ? "Prueba con otro nombre." : chat.showTrash ? "Los chats que elimines aparecerán aquí." : "Empieza un chat para verlo aquí.")
                .font(.system(size: 11)).foregroundStyle(.secondary)
        }.multilineTextAlignment(.center).padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var conversations: some View {
        List(selection: Binding<UUID?>(get: { chat.showTrash ? nil : chat.conversationID }, set: { id in
            guard let id, let entry = chat.conversations.first(where: { $0.id == id }), !chat.showTrash else { return }
            Task { await chat.open(entry, model: model) }
        })) {
            ForEach(chat.conversations) { entry in
                HStack(alignment: .center, spacing: 10) {
                    Image(systemName: chat.showTrash ? "trash" : "bubble.left")
                        .foregroundStyle(.secondary).frame(width: 18)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(verbatim: entry.title).font(.system(size: 12, weight: .medium)).lineLimit(1)
                        Text(date(entry.updatedAt)).font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    if chat.showTrash {
                        Button { Task { await chat.setTrashed(entry, model: model, trashed: false) } } label: {
                            Image(systemName: "arrow.uturn.backward")
                        }.buttonStyle(.borderless).help("Restaurar chat")
                            .accessibilityLabel("Restaurar \(entry.title)").disabled(!model.canManageChatHistory)
                    }
                }.padding(.vertical, 7).tag(entry.id).help(entry.title)
                    .contextMenu {
                        JarvisChatActionsView(entry: entry, model: model) { renameEntry = entry }
                            .disabled(!model.canManageChatHistory)
                    }
            }
            if chat.nextOffset != nil {
                Button("Cargar más chats") { Task { await chat.refreshHistory(model: model, append: true) } }
                    .font(.system(size: 11)).disabled(chat.historyLoading)
            }
        }.listStyle(.sidebar).scrollContentBackground(.hidden).disabled(!model.canManageChatHistory)
            .overlay(alignment: .topTrailing) {
                if chat.historyLoading { ProgressView().controlSize(.small).padding(10) }
            }
    }

    private func date(_ raw: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        return date?.formatted(.dateTime.day().month(.abbreviated).hour().minute()) ?? ""
    }
}
