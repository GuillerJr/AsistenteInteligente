import AegisAudioCore
import AppKit
import SwiftUI

struct JarvisChatView: View {
    let model: MenuBarModel
    var interactive = true
    @State private var selectingFiles = false
    @State private var renameEntry: ChatConversationSummary?
    @State private var confirmingDelete = false
    @State private var workspacePanel: JarvisWorkspacePanel?
    @State private var sideChat = SideChatStore()
    @State private var showPalette = false
    @State private var browser: JarvisBrowserTabs
    @FocusState private var composerFocused: Bool
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    private var chat: JarvisChatStore { model.chat }
    private var showBrowser: Bool {
        get { workspacePanel == .browser }
        nonmutating set { if newValue { workspacePanel = .browser } else if showBrowser { workspacePanel = nil } }
    }
    private var showSideChat: Bool {
        get { workspacePanel == .sideChat }
        nonmutating set { if newValue { workspacePanel = .sideChat } else if showSideChat { workspacePanel = nil } }
    }
    private var browserVisible: Bool { showBrowser && chat.pane == .chat }
    private var sideChatVisible: Bool { showSideChat && chat.pane == .chat }
    private var panelVisible: Bool { workspacePanel != nil && chat.pane == .chat }
    private var accent: Color { HUDStyle.accent(for: .active) }

    init(model: MenuBarModel, interactive: Bool = true, previewBrowser: Bool = false,
         previewPanel: JarvisWorkspacePanel? = nil) {
        self.model = model
        self.interactive = interactive
        _workspacePanel = State(initialValue: previewPanel ?? (previewBrowser ? .browser : nil))
        _browser = State(initialValue: JarvisBrowserTabs(persistent: interactive))
    }

    var body: some View {
        GeometryReader { geometry in
        let sidebarVisible = chat.showHistory
            && (!panelVisible || geometry.size.width >= 1_120)
        HStack(spacing: 0) {
            if sidebarVisible {
                JarvisChatHistoryView(model: model, interactive: interactive).frame(minWidth: 250, idealWidth: 270, maxWidth: 290)
                    .layoutPriority(1)
                Divider()
            }
            switch chat.pane {
            case .chat: chatPanel(sidebarVisible: sidebarVisible)
            case .dot: JarvisDotView(model: model)
            case .scheduled: JarvisScheduledView(model: model)
            case .project(let id): JarvisProjectView(model: model, projectID: id)
            }
            if browserVisible {
                Divider()
                JarvisBrowserPanel(tabs: browser, chat: chat, isVisible: { browserVisible }) { showBrowser = false }
            }
            if sideChatVisible {
                Divider()
                SideChatPanel(model: model, store: sideChat) { showSideChat = false }
            }
            if workspacePanel == .context && chat.pane == .chat {
                Divider()
                JarvisChatContextView(model: model, interactive: interactive,
                    close: { workspacePanel = nil }, attach: { selectingFiles = true },
                    showActions: openActionHistory)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: browserVisible ? 800 : panelVisible ? 742 : chat.showHistory ? 740 : 480,
               idealWidth: 940, minHeight: 460, idealHeight: 720)
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(JarvisTheme.ink)
        .tint(accent)
        .onAppear {
            if chat.browserPending { showBrowser = true; chat.browserPending = false }
            if chat.deletePending { confirmingDelete = true; chat.deletePending = false }
        }
        .onChange(of: chat.conversationID) { _, _ in
            composerFocused = true
        }
        .onChange(of: chat.composerDraft) { _, text in
            if text != nil { composerFocused = true }
        }
        .onChange(of: chat.browserRequest) { _, _ in showBrowser = true; chat.browserPending = false }
        .onChange(of: chat.searchFocusRequest) { _, _ in workspacePanel = nil }
        .onChange(of: chat.deleteRequest) { _, _ in confirmingDelete = true; chat.deletePending = false }
        .confirmationDialog(String(localized: "¿Eliminar este chat?", bundle: .module), isPresented: $confirmingDelete, titleVisibility: .visible) {
            if let entry = chat.currentConversation {
                Button(String(localized: "Mover a la papelera", bundle: .module), role: .destructive) {
                    Task { await chat.setTrashed(entry, model: model, trashed: true) }
                }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: { Text(String(localized: "Podrás restaurarlo desde la papelera del historial.", bundle: .module)) }
        .onChange(of: chat.abandonedTemporaryChats) { _, pending in
            guard interactive, !pending.isEmpty else { return }
            Task { await chat.deleteAbandonedTemporaryChats(model: model) }
        }
        .task {
            guard interactive else { return }
            await model.refreshDaemon()
            await chat.refreshHistory(model: model)
            composerFocused = true
        }
        .fileImporter(isPresented: $selectingFiles, allowedContentTypes: ChatAttachmentLoader.allowedTypes,
                      allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls): Task { await chat.addFiles(urls) }
            case .failure(let error):
                if (error as NSError).code != NSUserCancelledError { chat.error = error.localizedDescription }
            }
        }
        .sheet(item: $renameEntry) { JarvisRenameChatView(entry: $0, model: model) }
        .sheet(isPresented: $showPalette) {
            JarvisCommandPalette(commands: paletteCommands) { showPalette = false }
        }
        .background {
            Button(String(localized: "Paleta de comandos", bundle: .module)) { showPalette.toggle() }
                .keyboardShortcut("k", modifiers: .command).hidden().accessibilityHidden(true)
        }
    }

    private var paletteCommands: [JarvisPaletteCommand] {
        let manage = interactive && model.canManageChatHistory
        var commands = [
            JarvisPaletteCommand(id: "new", title: String(localized: "Nueva conversación", bundle: .module), symbol: "square.and.pencil",
                                 shortcut: "⌘N", keywords: ["chat", "nuevo"], enabled: manage) {
                chat.newConversation(); composerFocused = true
            },
            JarvisPaletteCommand(id: "temporary", title: String(localized: "Chat temporal", bundle: .module), symbol: "eye.slash",
                                 keywords: ["privado", "incognito", String(localized: "sin memoria", bundle: .module)],
                                 enabled: manage && !chat.temporary) {
                chat.newTemporaryConversation(); composerFocused = true
            },
            JarvisPaletteCommand(id: "browser", title: showBrowser ? String(localized: "Cerrar navegador", bundle: .module) : String(localized: "Abrir navegador", bundle: .module),
                                 symbol: "globe", keywords: ["web", "pagina", "internet", "url"],
                                 enabled: true) { showBrowser.toggle(); chat.pane = .chat },
            JarvisPaletteCommand(id: "context", title: String(localized: "Contexto del chat", bundle: .module),
                                 symbol: "sidebar.right", keywords: ["proyecto", "archivos", "instrucciones"]) {
                workspacePanel = workspacePanel == .context ? nil : .context; chat.pane = .chat
            },
            JarvisPaletteCommand(id: "search", title: String(localized: "Buscar chats", bundle: .module),
                                 symbol: "magnifyingglass", shortcut: "⌘F", keywords: ["historial", "mensaje"]) {
                workspacePanel = nil; chat.showHistory = true
                chat.searchFocusPending = true; chat.searchFocusRequest = UUID()
            },
            JarvisPaletteCommand(id: "actions", title: String(localized: "Historial de acciones", bundle: .module),
                                 symbol: "clock.arrow.circlepath", keywords: ["actividad", "herramientas"],
                                 enabled: interactive, run: openActionHistory),
            JarvisPaletteCommand(id: "dot", title: String(localized: "Abrir Dot", bundle: .module), symbol: "circle.fill",
                                 keywords: ["autonomo", "actividad", "24/7", "pausar"],
                                 enabled: interactive) { chat.pane = .dot },
            JarvisPaletteCommand(id: "scheduled", title: String(localized: "Tareas programadas", bundle: .module), symbol: "clock",
                                 keywords: ["programar", "automatizacion", "recordatorio"],
                                 enabled: interactive) { chat.pane = .scheduled },
            JarvisPaletteCommand(id: "plan", title: chat.planMode ? String(localized: "Salir del modo plan", bundle: .module) : String(localized: "Modo plan", bundle: .module),
                                 symbol: "list.bullet.clipboard", keywords: ["planear", "pasos", "plan"],
                                 enabled: true) { chat.planMode.toggle(); composerFocused = true },
            JarvisPaletteCommand(id: "side", title: showSideChat ? String(localized: "Cerrar chat lateral", bundle: .module) : String(localized: "Chat lateral", bundle: .module),
                                 symbol: "sidebar.right", keywords: ["pregunta", "rapida", "lado"],
                                 enabled: manage) { showSideChat.toggle() },
            JarvisPaletteCommand(id: "history", title: chat.showHistory ? String(localized: "Ocultar historial", bundle: .module) : String(localized: "Mostrar historial", bundle: .module),
                                 symbol: "sidebar.left", keywords: ["conversaciones", "buscar"]) {
                chat.showHistory.toggle()
            },
            JarvisPaletteCommand(id: "pending", title: String(localized: "Ver lo que requiere tu atención", bundle: .module),
                                 symbol: "exclamationmark.bubble",
                                 keywords: [String(localized: "pendientes", bundle: .module), "aprobaciones", "atencion"], enabled: interactive) {
                chat.showHistory = true
                Task { await chat.refreshAttention(model: model) }
            },
            JarvisPaletteCommand(id: "meeting",
                                 title: model.meetingRecording ? String(localized: "Terminar notas de reunión", bundle: .module) : String(localized: "Tomar notas de una reunión", bundle: .module),
                                 symbol: model.meetingRecording ? "stop.circle" : "record.circle",
                                 keywords: ["grabar", "reunion", "transcribir"],
                                 enabled: interactive && (model.meetingRecording || (manage && !chat.isBusy))) {
                Task { await model.toggleMeetingNotes() }
            },
            JarvisPaletteCommand(id: "voice", title: String(localized: "Hablar con Jarvis", bundle: .module), symbol: "mic",
                                 keywords: ["voz", "escuchar", "dictar"], enabled: interactive && model.canStartVoiceTurn) {
                Task { await model.startVoiceTurn() }
            },
            JarvisPaletteCommand(id: "attach", title: String(localized: "Adjuntar archivos", bundle: .module), symbol: "paperclip",
                                 keywords: ["documento", "pdf", "imagen"], enabled: manage) {
                selectingFiles = true
            },
            JarvisPaletteCommand(id: "quick", title: String(localized: "Pregunta rápida desde cualquier app", bundle: .module), symbol: "sparkles",
                                 shortcut: "⌃⇧J", keywords: ["mini", "flotante"], enabled: interactive) {
                QuickEntryPanelController.shared.toggle(model: model)
            },
            JarvisPaletteCommand(id: "settings", title: String(localized: "Abrir ajustes", bundle: .module), symbol: "gearshape", shortcut: "⌘,",
                                 keywords: ["configuracion", "salud", "diagnostico", "integraciones"], enabled: interactive) {
                RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
            },
        ]
        if let entry = chat.currentConversation {
            commands.insert(JarvisPaletteCommand(id: "rename", title: String(localized: "Renombrar este chat", bundle: .module), symbol: "pencil",
                                                 keywords: ["titulo", "nombre"], enabled: manage) {
                renameEntry = entry
            }, at: 2)
        }
        if chat.isBusy {
            commands.insert(JarvisPaletteCommand(id: "cancel", title: String(localized: "Detener la respuesta", bundle: .module), symbol: "stop.fill",
                                                 keywords: ["cancelar", "parar"], enabled: interactive) {
                Task { await model.cancelChatResponse() }
            }, at: 0)
        }
        return commands
    }

    private func chatPanel(sidebarVisible: Bool) -> some View {
        VStack(spacing: 0) {
            header(sidebarVisible: sidebarVisible)
            JarvisSeparator()
            transcript
            if model.interactionID != nil {
                Button {
                    RequestedWindowFocus.open("approval") { openWindow(id: "approval") }
                } label: {
                    Label(model.pendingApproval != nil ? String(localized: "Revisar acción pendiente", bundle: .module) : String(localized: "Revisar solicitud", bundle: .module),
                          systemImage: "exclamationmark.bubble")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .warning)))
                .padding(.horizontal, 20).padding(.bottom, 12)
            }
            if chat.transcript.hasNewerHistory {
                HStack(spacing: 12) {
                    Label(String(localized: "Viendo mensajes anteriores", bundle: .module), systemImage: "clock.arrow.circlepath")
                        .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    Spacer()
                    Button(String(localized: "Volver a recientes", bundle: .module)) { Task { await chat.loadRecent(model: model) } }
                        .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                }.padding(.horizontal, 20).padding(.top, 12)
            }
            if let notice = chat.notice {
                HStack(alignment: .top, spacing: 8) {
                    Label(notice, systemImage: "checkmark.circle").font(.system(size: 12))
                    Spacer()
                    Button { chat.notice = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel(String(localized: "Cerrar confirmación", bundle: .module))
                }.foregroundStyle(accent).padding(.horizontal, 20).padding(.top, 12)
            }
            if let warning = chat.appGrantWarning {
                HStack(alignment: .top, spacing: 8) {
                    Label(warning, systemImage: "exclamationmark.triangle").font(.system(size: 12))
                    Spacer()
                    Button { chat.appGrantWarning = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel(String(localized: "Cerrar aviso de permiso", bundle: .module))
                }.foregroundStyle(HUDStyle.accent(for: .warning)).padding(.horizontal, 20).padding(.top, 12)
            }
            if model.daemonState != .online {
                Label(String(localized: "Jarvis está sin conexión. Tu borrador se conserva mientras se restablece el servicio.", bundle: .module),
                      systemImage: "wifi.slash")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    .padding(.horizontal, 20).padding(.top, 10)
            }
            if let error = chat.error {
                HStack(alignment: .top, spacing: 8) {
                    Label(error, systemImage: "exclamationmark.triangle").font(.system(size: 12))
                    Spacer()
                    if chat.retryAvailable {
                        Button(String(localized: "Reintentar", bundle: .module)) { Task { await model.retryChatMessage() } }
                            .buttonStyle(MenuBarSmallButtonStyle()).disabled(!interactive)
                    }
                    Button { chat.error = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel(String(localized: "Cerrar aviso", bundle: .module))
                }.foregroundStyle(HUDStyle.accent(for: .warning)).padding(.horizontal, 20).padding(.top, 12)
            }
            JarvisChatComposerView(model: model, composerFocused: $composerFocused, interactive: interactive) {
                selectingFiles = true
            }
        }
        .frame(minWidth: panelVisible ? 360 : 450)
        // Like Codex, web links in replies open in the built-in browser.
        .environment(\.openURL, OpenURLAction { url in
            guard ["http", "https"].contains(url.scheme?.lowercased()) else { return .systemAction }
            browser.openFromChat(url)
            showBrowser = true
            return .handled
        })
    }

    private func openActionHistory() {
        guard interactive else { return }
        RequestedWindowFocus.open("action-history") { openWindow(id: "action-history") }
    }

    private func header(sidebarVisible: Bool) -> some View {
        HStack(spacing: 8) {
            MenuBarIconButton(symbol: "sidebar.left", title: sidebarVisible ? String(localized: "Ocultar historial", bundle: .module) : String(localized: "Mostrar historial", bundle: .module)) {
                if sidebarVisible { chat.showHistory = false }
                else { workspacePanel = nil; chat.showHistory = true }
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: chat.currentTitle).font(.system(size: 14, weight: .semibold))
                    .lineLimit(1).help(chat.currentTitle)
                Text(chat.temporary ? String(localized: "Chat temporal · no se guarda ni usa tu memoria", bundle: .module)
                     : chat.selectionLoading ? String(localized: "Cargando conversación", bundle: .module)
                     : chat.isBusy ? (model.pendingApproval != nil
                                     ? String(localized: "Esperando tu aprobación", bundle: .module)
                                     : String(localized: "Preparando respuesta", bundle: .module))
                     : chat.workspaceProject?.name ?? (model.canSendChatMessage
                         ? String(localized: "Listo para conversar", bundle: .module) : model.presentationStatus.title))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary).lineLimit(1)
            }.frame(maxWidth: .infinity, alignment: .leading).layoutPriority(1)
            if model.meetingRecording {
                MenuBarIconButton(symbol: "stop.circle.fill", title: String(localized: "Terminar notas de reunión", bundle: .module)) {
                    Task { await model.toggleMeetingNotes() }
                }.foregroundStyle(JarvisTheme.failure).disabled(!interactive)
            }
            MenuBarIconButton(symbol: "globe", title: showBrowser ? String(localized: "Cerrar navegador", bundle: .module) : String(localized: "Navegador", bundle: .module)) {
                showBrowser.toggle()
            }.accessibilityIdentifier("chat-browser-toggle")
            MenuBarIconButton(symbol: "sidebar.right", title: String(localized: "Contexto del chat", bundle: .module)) {
                workspacePanel = workspacePanel == .context ? nil : .context
            }.accessibilityIdentifier("chat-context-toggle")
                .accessibilityValue(workspacePanel == .context ? String(localized: "Visible", bundle: .module) : String(localized: "Oculto", bundle: .module))
            Menu {
                Button(String(localized: "Nueva conversación", bundle: .module)) { chat.newConversation(); composerFocused = true }
                    .disabled(!model.canManageChatHistory)
                Button(String(localized: "Chat temporal", bundle: .module)) { chat.newTemporaryConversation(); composerFocused = true }
                    .disabled(!model.canManageChatHistory || chat.temporary)
                Button(String(localized: "Chat lateral", bundle: .module)) { showSideChat.toggle() }
                    .disabled(!model.canManageChatHistory || !interactive)
                Divider()
                Button(String(localized: "Paleta de comandos", bundle: .module)) { showPalette = true }
                Button(String(localized: "Historial de acciones", bundle: .module), action: openActionHistory).disabled(!interactive)
                Button(model.meetingRecording ? String(localized: "Terminar notas de reunión", bundle: .module) : String(localized: "Tomar notas de una reunión", bundle: .module)) {
                    Task { await model.toggleMeetingNotes() }
                }.disabled(!interactive || (!model.meetingRecording && (!model.canManageChatHistory || chat.isBusy)))
                if let entry = chat.currentConversation {
                    Divider()
                    JarvisChatActionsView(entry: entry, model: model) { renameEntry = entry }
                        .disabled(!interactive || !model.canManageChatHistory)
                }
            } label: { Image(systemName: "ellipsis").frame(width: 24, height: 24) }
                .menuStyle(.borderlessButton).fixedSize()
                .accessibilityLabel(String(localized: "Más opciones del chat", bundle: .module))
                .help(String(localized: "Más opciones del chat", bundle: .module))
            if !sidebarVisible { newConversationButton(compact: true) }
        }.padding(.horizontal, 14).frame(height: 52)
    }

    private func newConversationButton(compact: Bool) -> some View {
        Button {
            chat.newConversation()
            composerFocused = true
        } label: {
            if compact { Image(systemName: "square.and.pencil").frame(width: 16) }
            else { Label(String(localized: "Nueva conversación", bundle: .module), systemImage: "square.and.pencil").fixedSize() }
        }
        .buttonStyle(JarvisIconButtonStyle())
        .disabled(!model.canManageChatHistory)
        .help(String(localized: "Empezar otra conversación con contexto independiente", bundle: .module))
        .accessibilityLabel(String(localized: "Nueva conversación", bundle: .module))
        .keyboardShortcut("n", modifiers: .command)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if chat.transcript.messages.isEmpty {
                    JarvisChatWelcomeView(chat: chat) { starter in
                        if chat.prepareStarter(starter) { composerFocused = true }
                    }.frame(maxWidth: .infinity).containerRelativeFrame(.vertical)
                } else {
                    LazyVStack(spacing: 22) {
                        if chat.nextBeforeSequence != nil {
                            Button(String(localized: "Cargar mensajes anteriores", bundle: .module)) { Task { await chat.loadOlder(model: model) } }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                        }
                        ForEach(chat.transcript.messages) { message in messageView(message).id(message.id) }
                        Color.clear.frame(height: 1).id("latest")
                    }.frame(maxWidth: 700).frame(maxWidth: .infinity).padding(.horizontal, 24).padding(.vertical, 20)
                }
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: chat.transcript.messages.last?.id) { _, _ in
                if !chat.transcript.hasNewerHistory { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .onChange(of: chat.conversationID) { _, _ in
                if chat.historyMatchNavigation == nil { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .task(id: chat.historyMatchNavigation?.id) {
                guard let request = chat.historyMatchNavigation else { return }
                await chat.navigateToHistoryMatch(request, afterLayout: {
                    // Let SwiftUI install the restored message IDs before asking
                    // ScrollViewReader to reveal the matching message.
                    await withCheckedContinuation { continuation in
                        DispatchQueue.main.async { continuation.resume() }
                    }
                }, scroll: { id in proxy.scrollTo(id, anchor: .bottom) })
            }
        }
    }

    /// Owner turns use a quiet bubble; replies remain readable across the canvas.
    private func messageView(_ message: TextChatTranscript.Message) -> some View {
        let fromOwner = message.role == .user
        return HStack(alignment: .top, spacing: 0) {
            if fromOwner { Spacer(minLength: 80) }
            VStack(alignment: fromOwner ? .trailing : .leading, spacing: 8) {
                if !message.attachmentNames.isEmpty {
                    ForEach(message.attachmentNames, id: \.self) { name in
                        Label { Text(verbatim: name).lineLimit(1).truncationMode(.middle) } icon: {
                            Image(systemName: "paperclip")
                        }
                        .font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary)
                        .padding(.horizontal, 9).padding(.vertical, 5)
                        .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 7))
                    }
                }
                if message.text.isEmpty {
                    ProgressView().controlSize(.small).accessibilityLabel(String(localized: "Jarvis está preparando la respuesta", bundle: .module))
                } else if fromOwner {
                    Text(verbatim: message.text).font(.system(size: 14)).lineSpacing(3)
                        .foregroundStyle(JarvisTheme.ink)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 14).padding(.vertical, 9)
                        .background(JarvisTheme.userBubble, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .strokeBorder(contrast == .increased ? JarvisTheme.hairlineStrong : .clear)
                            .allowsHitTesting(false))
                } else {
                    JarvisChatResponseView(message.text).foregroundStyle(JarvisTheme.ink)
                    if chat.planReplies.contains(message.id),
                       chat.transcript.jobID == nil || message.id != chat.transcript.messages.last?.id {
                        JarvisPlanCard(text: message.text, implemented: chat.implementedPlans.contains(message.id)) { steps in
                            chat.implementedPlans.insert(message.id)
                            chat.planMode = false
                            let numbered = steps.enumerated().map { "\($0.offset + 1). \($0.element)" }
                            Task { await model.sendChatMessage(String(localized: "Implementa este plan:\n", bundle: .module) + numbered.joined(separator: "\n")) }
                        }
                    }
                }
                if message.interrupted {
                    Label(String(localized: "Solicitud detenida", bundle: .module), systemImage: "stop.circle")
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                }
                if message.truncated {
                    Text(String(localized: "Este mensaje se muestra parcialmente.", bundle: .module)).font(.system(size: 11))
                        .foregroundStyle(JarvisTheme.inkSecondary)
                }
                if !message.text.isEmpty {
                    HStack(spacing: 3) {
                        MenuBarIconButton(symbol: "doc.on.doc", title: String(localized: "Copiar mensaje", bundle: .module)) {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(message.text, forType: .string)
                        }
                        if chat.replayTarget(for: message) != nil {
                            MenuBarIconButton(symbol: fromOwner ? "pencil" : "arrow.clockwise",
                                              title: fromOwner ? String(localized: "Editar desde este mensaje", bundle: .module) : String(localized: "Regenerar respuesta", bundle: .module)) {
                                Task { await chat.replay(message, regenerate: !fromOwner, model: model) }
                            }.disabled(!interactive || !model.canManageChatHistory)
                        }
                        if !fromOwner && message.sequence != nil && !message.interrupted {
                            feedbackButton(message, value: "helpful", symbol: "hand.thumbsup",
                                           title: String(localized: "Respuesta útil", bundle: .module))
                            feedbackButton(message, value: "unhelpful", symbol: "hand.thumbsdown",
                                           title: String(localized: "Respuesta no útil", bundle: .module))
                        }
                    }.padding(.leading, -6)
                }
            }
            .frame(maxWidth: fromOwner ? 520 : .infinity, alignment: fromOwner ? .trailing : .leading)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(fromOwner ? String(localized: "Tú", bundle: .module) : String(localized: "Jarvis", bundle: .module))
    }

    private func feedbackButton(_ message: TextChatTranscript.Message, value: String,
                                symbol: String, title: String) -> some View {
        MenuBarIconButton(symbol: message.feedback == value ? symbol + ".fill" : symbol, title: title) {
            Task { await chat.rate(message, value: value, model: model) }
        }
        .disabled(!interactive || !model.canManageChatHistory || chat.feedbackUpdating.contains(message.id))
        .accessibilityValue(message.feedback == value ? String(localized: "Seleccionada", bundle: .module) : String(localized: "Sin seleccionar", bundle: .module))
    }

}
