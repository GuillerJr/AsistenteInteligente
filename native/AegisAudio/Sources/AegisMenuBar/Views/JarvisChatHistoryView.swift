import AegisAudioCore
import SwiftUI

struct JarvisChatHistoryView: View {
    let model: MenuBarModel
    @State private var renameEntry: ChatConversationSummary?
    @State private var confirmingEmptyTrash = false
    @State private var creatingProject = false
    @FocusState private var searchFocused: Bool
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            controls.padding(.horizontal, 10).padding(.top, 12).padding(.bottom, 8)
            if !chat.attention.isEmpty && !chat.showTrash { attentionSection }
            Text(chat.showTrash ? "Papelera" : "Recientes")
                .font(.system(size: 11, weight: .medium)).foregroundStyle(JarvisTheme.inkMuted)
                .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 2)
            if chat.conversations.isEmpty { emptyState }
            else { conversations }
            JarvisSeparator()
            VStack(alignment: .leading, spacing: 4) {
                if chat.showTrash {
                    Button(role: .destructive) { confirmingEmptyTrash = true } label: {
                        Label("Vaciar papelera", systemImage: "trash")
                    }.buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
                }
                Button { chat.showTrash.toggle() } label: {
                    Label(chat.showTrash ? "Volver a los chats" : "Papelera",
                          systemImage: chat.showTrash ? "chevron.backward" : "trash")
                }.buttonStyle(JarvisRowButtonStyle())
                Label(chat.showTrash ? "Restaura un chat para continuar." : "Historial cifrado en este Mac",
                      systemImage: chat.showTrash ? "arrow.uturn.backward" : "lock.shield")
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 10).padding(.top, 4)
            }.padding(10)
        }
        .background(JarvisTheme.sidebar)
        .sheet(item: $renameEntry) { JarvisRenameChatView(entry: $0, model: model) }
        .sheet(isPresented: $creatingProject) {
            JarvisProjectEditor(project: nil) { name, color, icon in
                let before = Set(chat.projects.map(\.id))
                let error = await chat.projectCall("projects.save", ["name": name, "color": color, "icon": icon],
                                                   model: model)
                if error == nil, let created = chat.projects.first(where: { !before.contains($0.id) }) {
                    chat.pane = .project(created.project_id)
                }
                return error
            }
        }
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
                await chat.projectCall("projects.list", model: model)
                do { try await Task.sleep(for: .seconds(60)) } catch { return }
            }
        }
        .task(id: "\(chat.historyQuery)|\(chat.showTrash)") {
            do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            await chat.refreshHistory(model: model)
        }
    }

    private var attentionSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text("Requiere tu atención").font(.system(size: 11, weight: .medium))
                    .foregroundStyle(JarvisTheme.inkMuted)
                Text("\(chat.attention.count)").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.white).padding(.horizontal, 5).padding(.vertical, 1)
                    .background(JarvisTheme.clay, in: Capsule())
            }.padding(.horizontal, 10)
            ForEach(chat.attention) { entry in
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.title).font(.system(size: 12.5, weight: .medium)).lineLimit(1)
                        .foregroundStyle(JarvisTheme.ink)
                    Text(entry.reason).font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary)
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
                .padding(10)
                .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusControl))
                .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusControl).strokeBorder(JarvisTheme.hairline))
            }
        }.padding(.horizontal, 10).padding(.vertical, 8)
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle().fill(JarvisTheme.clay).frame(width: 10, height: 10).accessibilityHidden(true)
                Text("Jarvis").font(.system(size: 14, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                Spacer()
                MenuBarIconButton(symbol: "arrow.clockwise", title: "Actualizar historial") {
                    Task { await chat.refreshHistory(model: model) }
                }.disabled(chat.historyLoading || !model.canReadChatHistory)
            }.padding(.leading, 10).padding(.bottom, 6)
            Button {
                chat.newConversation()
                chat.pane = .chat
                chat.showTrash = false
            } label: { Label("Nuevo chat", systemImage: "square.and.pencil") }
                .buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
                .keyboardShortcut("n", modifiers: .command)
            Button { chat.pane = .dot } label: {
                Label { Text("Dot") } icon: { Circle().fill(JarvisTheme.clay).frame(width: 9, height: 9) }
            }.buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .dot))
                .help("Lo que Jarvis hace por su cuenta, a toda hora")
            Button { chat.pane = .scheduled } label: { Label("Programadas", systemImage: "clock") }
                .buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .scheduled))
            HStack {
                Text("Proyectos").font(.system(size: 11, weight: .medium)).foregroundStyle(JarvisTheme.inkMuted)
                Spacer()
                Button { creatingProject = true } label: { Image(systemName: "plus") }
                    .buttonStyle(JarvisIconButtonStyle()).help("Nuevo proyecto")
                    .disabled(!model.canManageChatHistory)
            }.padding(.leading, 10).padding(.top, 6)
            ForEach(chat.projects) { project in
                Button { chat.pane = .project(project.project_id) } label: {
                    Label { Text(project.name).lineLimit(1) } icon: {
                        Image(systemName: project.symbol).foregroundStyle(project.tint)
                    }
                }.buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .project(project.project_id)))
            }
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(JarvisTheme.inkMuted)
                TextField("Buscar chats", text: Binding(get: { chat.historyQuery },
                                                       set: { chat.historyQuery = String($0.prefix(128)) }))
                    .textFieldStyle(.plain).focused($searchFocused).accessibilityLabel("Buscar conversaciones")
                if !chat.historyQuery.isEmpty {
                    Button { chat.historyQuery = "" } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(JarvisTheme.inkSecondary).accessibilityLabel("Limpiar búsqueda")
                }
            }.jarvisInput(focused: searchFocused)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            if chat.historyLoading { ProgressView().controlSize(.small) }
            else { Image(systemName: !chat.historyQuery.isEmpty ? "magnifyingglass" : chat.showTrash ? "trash" : "bubble.left.and.bubble.right")
                    .font(.system(size: 25)).foregroundStyle(JarvisTheme.inkMuted) }
            Text(chat.historyLoading ? "Cargando historial…" : !chat.historyQuery.isEmpty ? "Sin coincidencias" : chat.showTrash ? "Papelera vacía" : "Tu primera conversación")
                .font(.system(size: 13, weight: .medium))
            Text(!chat.historyQuery.isEmpty ? "Prueba con otro nombre." : chat.showTrash ? "Los chats que elimines aparecerán aquí." : "Empieza un chat para verlo aquí.")
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
        }.multilineTextAlignment(.center).padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var conversations: some View {
        // Own rows, so the selection is a warm wash instead of the system blue.
        ScrollView {
            LazyVStack(spacing: 1) {
                ForEach(chat.conversations) { entry in
                    Button {
                        guard !chat.showTrash else { return }
                        Task { await chat.open(entry, model: model) }
                    } label: {
                        HStack(alignment: .center, spacing: 8) {
                            Text(verbatim: entry.title).font(.system(size: 13)).lineLimit(1)
                                .foregroundStyle(JarvisTheme.ink)
                            Spacer(minLength: 0)
                            if chat.showTrash {
                                Button { Task { await chat.setTrashed(entry, model: model, trashed: false) } } label: {
                                    Image(systemName: "arrow.uturn.backward")
                                }.buttonStyle(.borderless).help("Restaurar chat")
                                    .accessibilityLabel("Restaurar \(entry.title)").disabled(!model.canManageChatHistory)
                            }
                        }
                    }
                    .buttonStyle(JarvisRowButtonStyle(selected: !chat.showTrash && chat.pane == .chat
                                                      && entry.id == chat.conversationID))
                    .help("\(entry.title) · \(date(entry.updatedAt))")
                    .contextMenu {
                        JarvisChatActionsView(entry: entry, model: model) { renameEntry = entry }
                            .disabled(!model.canManageChatHistory)
                    }
                }
                if chat.nextOffset != nil {
                    Button("Cargar más chats") { Task { await chat.refreshHistory(model: model, append: true) } }
                        .buttonStyle(JarvisRowButtonStyle()).disabled(chat.historyLoading)
                }
            }
            .padding(.horizontal, 10)
        }
        .disabled(!model.canManageChatHistory)
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
