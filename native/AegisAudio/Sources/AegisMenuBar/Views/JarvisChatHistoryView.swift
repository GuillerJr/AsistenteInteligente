import AegisAudioCore
import SwiftUI

struct JarvisChatHistoryView: View {
    let model: MenuBarModel
    var interactive = true
    @State private var renameEntry: ChatConversationSummary?
    @State private var confirmingEmptyTrash = false
    @State private var creatingProject = false
    @FocusState private var searchFocused: Bool
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        controls.padding(.horizontal, 10).padding(.top, 12).padding(.bottom, 8)
                        if !chat.attention.isEmpty && !chat.showTrash { attentionSection }
                        Text(chat.showTrash ? String(localized: "Papelera", bundle: .module) : chat.showArchived ? String(localized: "Archivados", bundle: .module) : String(localized: "Recientes", bundle: .module))
                            .font(.system(size: 11, weight: .medium)).foregroundStyle(JarvisTheme.inkMuted)
                            .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 2)
                        if chat.conversations.isEmpty { emptyState }
                        else { conversations }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                .scrollBounceBehavior(.basedOnSize)
                .scrollIndicators(.visible)
                .onAppear {
                    if chat.searchFocusPending {
                        proxy.scrollTo("history-search", anchor: .center)
                        searchFocused = true; chat.searchFocusPending = false
                    }
                }
                .onChange(of: chat.searchFocusRequest) { _, _ in
                    proxy.scrollTo("history-search", anchor: .center)
                    searchFocused = true; chat.searchFocusPending = false
                }
            }
            JarvisSeparator()
            VStack(alignment: .leading, spacing: 4) {
                if chat.showTrash {
                    Button(role: .destructive) { confirmingEmptyTrash = true } label: {
                        Label(String(localized: "Vaciar papelera", bundle: .module), systemImage: "trash")
                    }.buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
                }
                if !chat.showTrash {
                    Button { chat.showArchived.toggle() } label: {
                        Label(chat.showArchived ? String(localized: "Volver a recientes", bundle: .module) : String(localized: "Archivados", bundle: .module),
                              systemImage: chat.showArchived ? "chevron.backward" : "archivebox")
                    }.buttonStyle(JarvisRowButtonStyle())
                }
                Button { chat.showTrash.toggle() } label: {
                    Label(chat.showTrash ? String(localized: "Volver a los chats", bundle: .module) : String(localized: "Papelera", bundle: .module),
                          systemImage: chat.showTrash ? "chevron.backward" : "trash")
                }.buttonStyle(JarvisRowButtonStyle())
                Label(chat.showTrash ? String(localized: "Restaura un chat para continuar.", bundle: .module) : String(localized: "Historial cifrado en este Mac", bundle: .module),
                      systemImage: chat.showTrash ? "arrow.uturn.backward" : "lock.shield")
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 10).padding(.top, 4)
            }.padding(10).fixedSize(horizontal: false, vertical: true)
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
        .confirmationDialog(String(localized: "¿Vaciar toda la papelera?", bundle: .module), isPresented: $confirmingEmptyTrash,
                            titleVisibility: .visible) {
            Button(String(localized: "Eliminar todos los chats de la papelera", bundle: .module), role: .destructive) {
                Task { await chat.emptyTrash(model: model) }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: {
            Text(String(localized: "Se eliminarán permanentemente todos los chats de la papelera y sus documentos, incluidos los que no muestra la búsqueda. Esta acción no se puede deshacer.", bundle: .module))
        }
        .task {
            guard interactive else { return }
            // Tasks and goals change on their own schedule; check every minute.
            while !Task.isCancelled {
                await chat.refreshAttention(model: model)
                await chat.projectCall("projects.list", model: model)
                do { try await Task.sleep(for: .seconds(60)) } catch { return }
            }
        }
        .task(id: "\(chat.historyQuery)|\(chat.showTrash)|\(chat.showArchived)") {
            guard interactive else { return }
            do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            await chat.refreshHistory(model: model)
        }
    }

    private var attentionSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(String(localized: "Requiere tu atención", bundle: .module)).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(JarvisTheme.inkMuted)
                Text(String(localized: "\(chat.attention.count)", bundle: .module)).font(.system(size: 10, weight: .semibold))
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
                            Button(String(localized: "Abrir", bundle: .module)) { Task { await chat.openAttention(entry, model: model) } }
                        }
                        if entry.action != "review_approval" {
                            Button(entry.action == "run_task" ? String(localized: "Reintentar", bundle: .module) : String(localized: "Reanudar", bundle: .module)) {
                                Task { await chat.resolveAttention(entry, model: model) }
                            }
                        }
                        Button(String(localized: "Posponer", bundle: .module)) {
                            Task { await chat.refreshAttention(model: model, snoozing: entry.id) }
                        }.help(String(localized: "Ocultarlo durante una hora", bundle: .module))
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
                Text(String(localized: "Jarvis", bundle: .module)).font(.system(size: 14, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                Spacer()
                MenuBarIconButton(symbol: "arrow.clockwise", title: String(localized: "Actualizar historial", bundle: .module)) {
                    Task { await chat.refreshHistory(model: model) }
                }.disabled(chat.historyLoading || !model.canReadChatHistory)
            }.padding(.leading, 10).padding(.bottom, 6)
            Button {
                chat.newConversation()
                chat.pane = .chat
                chat.showTrash = false
            } label: { Label(String(localized: "Nuevo chat", bundle: .module), systemImage: "square.and.pencil") }
                .buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
            Button { chat.pane = .dot } label: {
                Label { Text(String(localized: "Dot", bundle: .module)) } icon: { Circle().fill(JarvisTheme.clay).frame(width: 9, height: 9) }
            }.buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .dot))
                .help(String(localized: "Lo que Jarvis hace por su cuenta, a toda hora", bundle: .module))
            Button { chat.pane = .scheduled } label: { Label(String(localized: "Programadas", bundle: .module), systemImage: "clock") }
                .buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .scheduled))
            HStack {
                Text(String(localized: "Proyectos", bundle: .module)).font(.system(size: 11, weight: .medium)).foregroundStyle(JarvisTheme.inkMuted)
                Spacer()
                Button { creatingProject = true } label: { Image(systemName: "plus") }
                    .buttonStyle(JarvisIconButtonStyle()).help(String(localized: "Nuevo proyecto", bundle: .module))
                    .accessibilityLabel(String(localized: "Nuevo proyecto", bundle: .module))
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
                TextField(String(localized: "Buscar chats", bundle: .module), text: Binding(get: { chat.historyQuery },
                                                       set: { chat.historyQuery = String($0.prefix(128)) }))
                    .textFieldStyle(.plain).focused($searchFocused).accessibilityLabel(String(localized: "Buscar conversaciones", bundle: .module))
                if !chat.historyQuery.isEmpty {
                    Button { chat.historyQuery = "" } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(JarvisTheme.inkSecondary).accessibilityLabel(String(localized: "Limpiar búsqueda", bundle: .module))
                }
            }.jarvisInput(focused: searchFocused).id("history-search")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            if chat.historyLoading { ProgressView().controlSize(.small) }
            else { Image(systemName: !chat.historyQuery.isEmpty ? "magnifyingglass" : chat.showTrash ? "trash" : "bubble.left.and.bubble.right")
                    .font(.system(size: 25)).foregroundStyle(JarvisTheme.inkMuted) }
            Text(chat.historyLoading ? String(localized: "Cargando historial…", bundle: .module) : !chat.historyQuery.isEmpty ? String(localized: "Sin coincidencias", bundle: .module) : chat.showTrash ? String(localized: "Papelera vacía", bundle: .module) : String(localized: "Tu primera conversación", bundle: .module))
                .font(.system(size: 13, weight: .medium))
            Text(!chat.historyQuery.isEmpty ? String(localized: "Prueba con otro nombre.", bundle: .module) : chat.showTrash ? String(localized: "Los chats que elimines aparecerán aquí.", bundle: .module) : String(localized: "Empieza un chat para verlo aquí.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
        }.multilineTextAlignment(.center).padding(20).frame(maxWidth: .infinity)
    }

    private var conversations: some View {
        // Own rows, so the selection is a warm wash instead of the system blue.
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
                            }.buttonStyle(.borderless).help(String(localized: "Restaurar chat", bundle: .module))
                                .accessibilityLabel(String(localized: "Restaurar \(entry.title)", bundle: .module)).disabled(!model.canManageChatHistory)
                        }
                    }
                }
                .buttonStyle(JarvisRowButtonStyle(selected: !chat.showTrash && chat.pane == .chat
                                                  && entry.id == chat.conversationID))
                .help(String(localized: "\(entry.title) · \(date(entry.updatedAt))", bundle: .module))
                .contextMenu {
                    JarvisChatActionsView(entry: entry, model: model) { renameEntry = entry }
                        .disabled(!model.canManageChatHistory)
                }
            }
            if chat.nextOffset != nil {
                Button(String(localized: "Cargar más chats", bundle: .module)) { Task { await chat.refreshHistory(model: model, append: true) } }
                    .buttonStyle(JarvisRowButtonStyle()).disabled(chat.historyLoading)
            }
        }
        .padding(.horizontal, 10)
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
