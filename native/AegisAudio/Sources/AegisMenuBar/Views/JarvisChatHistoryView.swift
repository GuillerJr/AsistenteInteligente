import AegisAudioCore
import SwiftUI

struct JarvisChatHistoryView: View {
    let model: MenuBarModel
    var interactive = true
    @State private var renameEntry: ChatConversationSummary?
    @State private var confirmingEmptyTrash = false
    @State private var creatingProject = false
    @State private var searchVisible = false
    @State private var projectsLoading = false
    @State private var projectsError: String?
    @State private var projectsExpanded = true
    @State private var historyExpanded = true
    @Environment(\.openWindow) private var openWindow
    @FocusState private var searchFocused: Bool
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            controls.padding(.horizontal, 10).padding(.top, 12).padding(.bottom, 8)
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    if !chat.attention.isEmpty && !chat.showTrash { attentionSection }
                    projectSection
                    if isFilteredHistory {
                        Text(verbatim: historyTitle)
                            .font(.system(size: 11, weight: .medium)).foregroundStyle(JarvisTheme.inkMuted)
                            .accessibilityAddTraits(.isHeader)
                            .padding(.horizontal, 20).padding(.top, 10).padding(.bottom, 4)
                    } else {
                        JarvisSidebarSectionHeader(title: historyTitle, expanded: $historyExpanded)
                            .padding(.horizontal, 10).padding(.top, 4)
                            .accessibilityIdentifier("sidebar-history-toggle")
                    }
                    if historyExpanded || isFilteredHistory {
                        if chat.conversations.isEmpty {
                            if chat.historyError == nil { emptyState }
                        } else { conversations }
                        historyFooter
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollIndicators(.visible)
            JarvisSeparator()
            navigationFooter
        }
        .background(JarvisTheme.sidebar)
        .onAppear {
            if chat.searchFocusPending { focusSearch() }
            else if !chat.historyCriteria.query.isEmpty { searchVisible = true }
            if case .project = chat.pane { projectsExpanded = true }
        }
        .onChange(of: chat.searchFocusRequest) { _, _ in focusSearch() }
        .onChange(of: chat.pane) { _, pane in
            if case .project = pane { projectsExpanded = true }
        }
        .sheet(item: $renameEntry) { JarvisRenameChatView(entry: $0, model: model) }
        .sheet(isPresented: $creatingProject) {
            JarvisProjectEditor(project: nil) { name, color, icon in
                guard interactive else {
                    return String(localized: "No se pudo actualizar el proyecto.", bundle: .module)
                }
                let before = Set(chat.projects.map(\.id))
                let error = await chat.projectCall("projects.save", ["name": name, "color": color, "icon": icon],
                                                   model: model)
                if error == nil, let created = chat.projects.first(where: { !before.contains($0.id) }) {
                    projectsError = nil
                    chat.pane = .project(created.project_id)
                }
                return error
            }
        }
        .confirmationDialog(String(localized: "¿Vaciar toda la papelera?", bundle: .module), isPresented: $confirmingEmptyTrash,
                            titleVisibility: .visible) {
            Button(String(localized: "Eliminar todos los chats de la papelera", bundle: .module), role: .destructive) {
                guard interactive else { return }
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
                await refreshProjects()
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
                            Button(String(localized: "Abrir", bundle: .module)) {
                                guard interactive else { return }
                                Task { await chat.openAttention(entry, model: model) }
                            }
                        }
                        if entry.action != "review_approval" {
                            Button(entry.action == "run_task" ? String(localized: "Reintentar", bundle: .module) : String(localized: "Reanudar", bundle: .module)) {
                                guard interactive else { return }
                                Task { await chat.resolveAttention(entry, model: model) }
                            }
                        }
                        Button(String(localized: "Posponer", bundle: .module)) {
                            guard interactive else { return }
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
                    guard interactive else { return }
                    Task { await chat.refreshHistory(model: model) }
                }.disabled(chat.historyLoading || !model.canReadChatHistory)
            }.padding(.leading, 10).padding(.bottom, 6)
            Button {
                chat.newConversation()
                chat.pane = .chat
                chat.showTrash = false
                chat.showArchived = false
            } label: { Label(String(localized: "Nuevo chat", bundle: .module), systemImage: "square.and.pencil") }
                .buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
                .accessibilityIdentifier("sidebar-new-chat")
            Button { focusSearch() } label: {
                HStack {
                    Label(String(localized: "Buscar chats", bundle: .module), systemImage: "magnifyingglass")
                    Spacer()
                    Text("⌘F").font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                        .accessibilityHidden(true)
                }
            }.buttonStyle(JarvisRowButtonStyle(selected: searchFocused))
                .accessibilityIdentifier("sidebar-search")
            Button {
                guard interactive else { return }
                chat.pane = .scheduled
            } label: { Label(String(localized: "Programadas", bundle: .module), systemImage: "clock") }
                .buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .scheduled))
                .accessibilityAddTraits(chat.pane == .scheduled ? .isSelected : [])
            Button {
                guard interactive else { return }
                chat.pane = .dot
            } label: {
                Label { Text(String(localized: "Dot", bundle: .module)) } icon: { Circle().fill(JarvisTheme.clay).frame(width: 9, height: 9) }
            }.buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .dot))
                .help(String(localized: "Lo que Jarvis hace por su cuenta, a toda hora", bundle: .module))
                .accessibilityAddTraits(chat.pane == .dot ? .isSelected : [])
            if searchVisible || !chat.historyCriteria.query.isEmpty {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass").foregroundStyle(JarvisTheme.inkMuted)
                        .accessibilityHidden(true)
                    TextField(String(localized: "Buscar en títulos y mensajes", bundle: .module), text: Binding(get: { chat.historyQuery },
                                                           set: { chat.historyQuery = String($0.prefix(128)) }))
                        .textFieldStyle(.plain).focused($searchFocused)
                        .accessibilityLabel(String(localized: "Buscar en títulos y mensajes", bundle: .module))
                        .accessibilityIdentifier("sidebar-search-field")
                        .onExitCommand {
                            if chat.historyCriteria.query.isEmpty { searchVisible = false; searchFocused = false }
                            else { chat.historyQuery = "" }
                        }
                    if !chat.historyCriteria.query.isEmpty {
                        Button { chat.historyQuery = "" } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.plain).foregroundStyle(JarvisTheme.inkSecondary)
                            .accessibilityLabel(String(localized: "Limpiar búsqueda", bundle: .module))
                    }
                }
                .jarvisInput(focused: searchFocused)
                .padding(.top, 4)
            }
        }
    }

    private var projectSection: some View {
        VStack(spacing: 2) {
            HStack(spacing: 0) {
                JarvisSidebarSectionHeader(title: String(localized: "Proyectos", bundle: .module),
                                          expanded: $projectsExpanded)
                    .accessibilityIdentifier("sidebar-projects-toggle")
                if projectsLoading {
                    ProgressView().controlSize(.mini)
                        .accessibilityLabel(String(localized: "Cargando proyectos", bundle: .module))
                }
                Button {
                    guard interactive else { return }
                    creatingProject = true
                } label: { Image(systemName: "plus") }
                    .buttonStyle(JarvisIconButtonStyle()).help(String(localized: "Nuevo proyecto", bundle: .module))
                    .accessibilityLabel(String(localized: "Nuevo proyecto", bundle: .module))
                    .disabled(!model.canManageChatHistory)
            }
            if projectsExpanded {
                ForEach(chat.projects) { project in
                    Button {
                        guard interactive else { return }
                        chat.pane = .project(project.project_id)
                    } label: {
                        Label { Text(verbatim: project.name).lineLimit(1) } icon: {
                            Image(systemName: project.symbol).foregroundStyle(project.tint)
                        }
                    }
                    .buttonStyle(JarvisRowButtonStyle(selected: chat.pane == .project(project.project_id)))
                    .accessibilityAddTraits(chat.pane == .project(project.project_id) ? .isSelected : [])
                    .accessibilityIdentifier("sidebar-project-\(project.project_id)")
                }
                if let projectsError {
                    Text(verbatim: projectsError).font(.system(size: 11))
                        .foregroundStyle(JarvisTheme.inkSecondary).padding(.horizontal, 10)
                    Button(String(localized: "Reintentar", bundle: .module)) {
                        Task { await refreshProjects() }
                    }.buttonStyle(JarvisRowButtonStyle()).disabled(projectsLoading || !model.canReadChatHistory)
                } else if chat.projects.isEmpty && !projectsLoading {
                    Button {
                        guard interactive else { return }
                        creatingProject = true
                    } label: {
                        Label(String(localized: "Crear un proyecto", bundle: .module), systemImage: "folder.badge.plus")
                    }.buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
                }
            }
        }.padding(.horizontal, 10)
    }

    private var navigationFooter: some View {
        VStack(alignment: .leading, spacing: 2) {
            if chat.showTrash {
                Button(role: .destructive) { confirmingEmptyTrash = true } label: {
                    Label(String(localized: "Vaciar papelera", bundle: .module), systemImage: "trash")
                }.buttonStyle(JarvisRowButtonStyle()).disabled(!model.canManageChatHistory)
            } else {
                Button { chat.showArchived.toggle() } label: {
                    Label(chat.showArchived ? String(localized: "Volver a recientes", bundle: .module) : String(localized: "Archivados", bundle: .module),
                          systemImage: chat.showArchived ? "chevron.backward" : "archivebox")
                }.buttonStyle(JarvisRowButtonStyle(selected: chat.showArchived))
            }
            Button { chat.showTrash.toggle() } label: {
                Label(chat.showTrash ? String(localized: "Volver a los chats", bundle: .module) : String(localized: "Papelera", bundle: .module),
                      systemImage: chat.showTrash ? "chevron.backward" : "trash")
            }.buttonStyle(JarvisRowButtonStyle(selected: chat.showTrash))
            HStack(spacing: 2) {
                Button {
                    guard interactive else { return }
                    RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
                } label: {
                    Label(String(localized: "Configuración", bundle: .module), systemImage: "gearshape")
                }.buttonStyle(JarvisRowButtonStyle())
                    .accessibilityIdentifier("sidebar-settings")
                MenuBarIconButton(symbol: "clock.arrow.circlepath", title: String(localized: "Historial de acciones", bundle: .module)) {
                    guard interactive else { return }
                    RequestedWindowFocus.open("action-history") { openWindow(id: "action-history") }
                }.accessibilityIdentifier("sidebar-action-history")
            }
            Label(chat.showTrash ? String(localized: "Restaura un chat para continuar.", bundle: .module) : String(localized: "Historial cifrado en este Mac", bundle: .module),
                  systemImage: chat.showTrash ? "arrow.uturn.backward" : "lock.shield")
                .font(.system(size: 10)).foregroundStyle(JarvisTheme.inkMuted)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 10).padding(.top, 4)
        }.padding(10).fixedSize(horizontal: false, vertical: true)
    }

    private var isFilteredHistory: Bool {
        !chat.historyCriteria.query.isEmpty || chat.showArchived || chat.showTrash
    }

    private var historyTitle: String {
        if chat.showTrash { return String(localized: "Papelera", bundle: .module) }
        if chat.showArchived { return String(localized: "Archivados", bundle: .module) }
        if !chat.historyCriteria.query.isEmpty { return String(localized: "Resultados", bundle: .module) }
        return String(localized: "Recientes", bundle: .module)
    }

    private func focusSearch() {
        searchVisible = true
        historyExpanded = true
        searchFocused = true
        chat.searchFocusPending = false
    }

    private func refreshProjects() async {
        guard interactive, !projectsLoading else { return }
        projectsLoading = true
        defer { projectsLoading = false }
        let error = await chat.projectCall("projects.list", model: model)
        guard !Task.isCancelled else { return }
        projectsError = error
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            if chat.historyLoading { ProgressView().controlSize(.small) }
            else { Image(systemName: !chat.historyCriteria.query.isEmpty ? "magnifyingglass" : chat.showTrash ? "trash" : "bubble.left.and.bubble.right")
                    .font(.system(size: 25)).foregroundStyle(JarvisTheme.inkMuted) }
            Text(chat.historyLoading ? String(localized: "Buscando en el historial…", bundle: .module) : chat.historySearchIncomplete ? String(localized: "Queda historial por revisar", bundle: .module) : !chat.historyCriteria.query.isEmpty ? String(localized: "Sin coincidencias", bundle: .module) : chat.showTrash ? String(localized: "Papelera vacía", bundle: .module) : String(localized: "Tu primera conversación", bundle: .module))
                .font(.system(size: 13, weight: .medium))
            Text(chat.historySearchIncomplete ? String(localized: "Continúa para buscar en los mensajes restantes.", bundle: .module) : !chat.historyCriteria.query.isEmpty ? String(localized: "Prueba con otras palabras del título o de los mensajes.", bundle: .module) : chat.showTrash ? String(localized: "Los chats que elimines aparecerán aquí.", bundle: .module) : String(localized: "Empieza un chat para verlo aquí.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
        }.multilineTextAlignment(.center).padding(20).frame(maxWidth: .infinity)
    }

    private var historyFooter: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let error = chat.historyError {
                Text(verbatim: error).font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                Button(String(localized: "Reintentar búsqueda", bundle: .module)) {
                    guard interactive else { return }
                    Task { await chat.refreshHistory(model: model) }
                }.buttonStyle(JarvisRowButtonStyle()).disabled(chat.historyLoading)
            }
            if chat.historyLoading && !chat.historyCriteria.query.isEmpty {
                Button(String(localized: "Detener búsqueda", bundle: .module)) { chat.pauseHistorySearch() }
                    .buttonStyle(JarvisRowButtonStyle())
            } else if chat.nextOffset != nil || chat.historySearchIncomplete {
                Button(chat.historyCriteria.query.isEmpty
                       ? String(localized: "Cargar más chats", bundle: .module)
                       : String(localized: "Seguir buscando", bundle: .module)) {
                    guard interactive else { return }
                    Task { await chat.refreshHistory(model: model, append: true) }
                }.buttonStyle(JarvisRowButtonStyle()).disabled(chat.historyLoading || !model.canReadChatHistory)
            }
        }.padding(.horizontal, 16).padding(.vertical, 6)
    }

    private var conversations: some View {
        LazyVStack(spacing: 1) {
            ForEach(JarvisSidebarHistory.groups(chat.conversations, query: chat.historyCriteria.query,
                                                archived: chat.showArchived, trash: chat.showTrash)) { group in
                if group.period != .results {
                    Text(verbatim: group.period.title)
                        .font(.system(size: 10)).foregroundStyle(JarvisTheme.inkMuted)
                        .accessibilityAddTraits(.isHeader)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 10).padding(.top, 8).padding(.bottom, 3)
                }
                ForEach(group.entries) { entry in conversationRow(entry) }
            }
        }
        .padding(.horizontal, 10)
        .disabled(!model.canManageChatHistory)
        .overlay(alignment: .topTrailing) {
            if chat.historyLoading { ProgressView().controlSize(.small).padding(10) }
        }
    }

    private func conversationRow(_ entry: ChatConversationSummary) -> some View {
        let selected = JarvisSidebarHistory.isSelected(entry, conversationID: chat.conversationID,
                                                       showingConversation: chat.pane == .chat,
                                                       trash: chat.showTrash)
        return HStack(spacing: 0) {
            if chat.showTrash {
                conversationTitle(entry).padding(.horizontal, 10)
                    .frame(maxWidth: .infinity, minHeight: JarvisTheme.rowHeight, alignment: .leading)
                    .accessibilityElement(children: .combine)
                Button {
                    guard interactive else { return }
                    Task { await chat.setTrashed(entry, model: model, trashed: false) }
                } label: {
                    Image(systemName: "arrow.uturn.backward")
                }
                .buttonStyle(JarvisIconButtonStyle()).help(String(localized: "Restaurar chat", bundle: .module))
                .accessibilityLabel(String(localized: "Restaurar \(entry.title)", bundle: .module))
                .disabled(!model.canManageChatHistory)
            } else {
                Button {
                    guard interactive else { return }
                    Task { await chat.open(entry, model: model) }
                } label: {
                    conversationTitle(entry)
                }
                .buttonStyle(JarvisRowButtonStyle(selected: selected))
                .accessibilityAddTraits(selected ? .isSelected : [])
                .accessibilityIdentifier("sidebar-chat-\(entry.id.uuidString)")
            }
        }
        .help(String(localized: "\(entry.title) · \(date(entry.updatedAt))", bundle: .module))
        .contextMenu {
            JarvisChatActionsView(entry: entry, model: model) { renameEntry = entry }
                .disabled(!interactive || !model.canManageChatHistory)
        }
    }

    private func conversationTitle(_ entry: ChatConversationSummary) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(verbatim: entry.title).font(.system(size: 13)).lineLimit(1)
                .foregroundStyle(JarvisTheme.ink)
            if let match = entry.searchMatch {
                Text(verbatim: match.excerpt).font(.system(size: 11)).lineLimit(2)
                    .foregroundStyle(JarvisTheme.inkSecondary)
            }
        }
    }

    private func date(_ raw: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        return date?.formatted(.dateTime.day().month(.abbreviated).hour().minute()) ?? ""
    }
}
