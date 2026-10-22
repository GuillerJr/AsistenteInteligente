import AegisAudioCore
import AppKit
import SwiftUI

struct JarvisChatView: View {
    let model: MenuBarModel
    var interactive = true
    var previewBrowser = false
    @State private var selectingFiles = false
    @State private var attachmentDropTarget = false
    @State private var renameEntry: ChatConversationSummary?
    @State private var confirmingDelete = false
    @State private var showSideChat = false
    @State private var sideChat = SideChatStore()
    @State private var showPalette = false
    @State private var showBrowser = false
    @State private var browser: JarvisBrowserTabs
    @FocusState private var composerFocused: Bool
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    private var chat: JarvisChatStore { model.chat }
    private var draft: String {
        get { chat.composerDraft ?? "" }
        nonmutating set { chat.composerDraft = newValue.isEmpty ? nil : newValue }
    }
    private var browserVisible: Bool { (showBrowser || previewBrowser) && chat.pane == .chat }
    private var sideChatVisible: Bool { showSideChat && chat.pane == .chat }
    private var accent: Color { HUDStyle.accent(for: .active) }
    private var validDraft: Bool {
        (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !chat.attachments.isEmpty)
            && draft.utf8.count <= 4_096
    }

    init(model: MenuBarModel, interactive: Bool = true, previewBrowser: Bool = false) {
        self.model = model
        self.interactive = interactive
        self.previewBrowser = previewBrowser
        _browser = State(initialValue: JarvisBrowserTabs(persistent: interactive))
    }

    var body: some View {
        GeometryReader { geometry in
        let sidebarVisible = chat.showHistory
            && (!browserVisible || geometry.size.width >= 1_120)
            && (!sideChatVisible || geometry.size.width >= 992)
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
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: browserVisible ? 800 : sideChatVisible ? 742 : chat.showHistory ? 740 : 480,
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
        .onChange(of: chat.searchFocusRequest) { _, _ in showBrowser = false; showSideChat = false }
        .onChange(of: showBrowser) { _, value in if value { showSideChat = false } }
        .onChange(of: showSideChat) { _, value in if value { showBrowser = false } }
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
        let manage = model.canManageChatHistory
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
            JarvisPaletteCommand(id: "dot", title: String(localized: "Abrir Dot", bundle: .module), symbol: "circle.fill",
                                 keywords: ["autonomo", "actividad", "24/7", "pausar"],
                                 enabled: true) { chat.pane = .dot },
            JarvisPaletteCommand(id: "scheduled", title: String(localized: "Tareas programadas", bundle: .module), symbol: "clock",
                                 keywords: ["programar", "automatizacion", "recordatorio"],
                                 enabled: true) { chat.pane = .scheduled },
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
                                 keywords: [String(localized: "pendientes", bundle: .module), "aprobaciones", "atencion"]) {
                chat.showHistory = true
                Task { await chat.refreshAttention(model: model) }
            },
            JarvisPaletteCommand(id: "meeting",
                                 title: model.meetingRecording ? String(localized: "Terminar notas de reunión", bundle: .module) : String(localized: "Tomar notas de una reunión", bundle: .module),
                                 symbol: model.meetingRecording ? "stop.circle" : "record.circle",
                                 keywords: ["grabar", "reunion", "transcribir"],
                                 enabled: model.meetingRecording || (manage && !chat.isBusy)) {
                Task { await model.toggleMeetingNotes() }
            },
            JarvisPaletteCommand(id: "voice", title: String(localized: "Hablar con Jarvis", bundle: .module), symbol: "mic",
                                 keywords: ["voz", "escuchar", "dictar"]) {
                Task { await model.startVoiceTurn() }
            },
            JarvisPaletteCommand(id: "attach", title: String(localized: "Adjuntar archivos", bundle: .module), symbol: "paperclip",
                                 keywords: ["documento", "pdf", "imagen"], enabled: !chat.isBusy) {
                selectingFiles = true
            },
            JarvisPaletteCommand(id: "quick", title: String(localized: "Pregunta rápida desde cualquier app", bundle: .module), symbol: "sparkles",
                                 shortcut: "⌃⇧J", keywords: ["mini", "flotante"]) {
                QuickEntryPanelController.shared.toggle(model: model)
            },
            JarvisPaletteCommand(id: "settings", title: String(localized: "Abrir ajustes", bundle: .module), symbol: "gearshape", shortcut: "⌘,",
                                 keywords: ["configuracion", "salud", "diagnostico", "integraciones"]) {
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
                                                 keywords: ["cancelar", "parar"]) {
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
            composer
        }
        .frame(minWidth: (showBrowser || previewBrowser) ? 360 : 480)
        // Like Codex, web links in replies open in the built-in browser.
        .environment(\.openURL, OpenURLAction { url in
            guard ["http", "https"].contains(url.scheme?.lowercased()) else { return .systemAction }
            browser.openFromChat(url)
            showBrowser = true
            return .handled
        })
    }

    private func header(sidebarVisible: Bool) -> some View {
        HStack(spacing: 12) {
            MenuBarIconButton(symbol: "sidebar.left", title: sidebarVisible ? String(localized: "Ocultar historial", bundle: .module) : String(localized: "Mostrar historial", bundle: .module)) {
                if sidebarVisible { chat.showHistory = false }
                else { showBrowser = false; showSideChat = false; chat.showHistory = true }
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(verbatim: chat.currentTitle).font(.system(size: 14, weight: .semibold))
                        .lineLimit(1).help(chat.currentTitle).layoutPriority(1)
                    if let project = chat.project(chat.currentConversation?.projectID?.uuidString.lowercased()
                                                  ?? chat.newChatProjectID) {
                        Button { chat.pane = .project(project.project_id) } label: {
                            Label(project.name, systemImage: project.symbol).font(.system(size: 11, weight: .medium))
                                .lineLimit(1).truncationMode(.tail)
                                .foregroundStyle(project.tint).padding(.horizontal, 7).padding(.vertical, 2)
                                .background(project.tint.opacity(0.13), in: Capsule())
                        }.buttonStyle(.plain).help(String(localized: "Este chat usa las instrucciones y archivos del proyecto", bundle: .module))
                    }
                }
                Text(chat.temporary ? String(localized: "Chat temporal · no se guarda ni usa tu memoria", bundle: .module)
                     : chat.selectionLoading ? String(localized: "Cargando conversación", bundle: .module)
                     : chat.isBusy ? (model.pendingApproval != nil
                                     ? String(localized: "Esperando tu aprobación", bundle: .module)
                                     : String(localized: "Preparando respuesta", bundle: .module))
                     : model.canSendChatMessage ? String(localized: "Listo para conversar", bundle: .module) : model.presentationStatus.title)
                    .font(.system(size: 11)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }.layoutPriority(1)
            Spacer(minLength: 8)
            if let entry = chat.currentConversation {
                Menu {
                    JarvisChatActionsView(entry: entry, model: model) { renameEntry = entry }
                } label: { Image(systemName: "ellipsis").frame(width: 16, height: 16) }
                    .menuStyle(.borderlessButton).fixedSize()
                    .disabled(!model.canManageChatHistory)
                    .help(String(localized: "Acciones del chat", bundle: .module)).accessibilityLabel(String(localized: "Acciones del chat", bundle: .module))
            }
            Button { showBrowser.toggle() } label: { Image(systemName: "globe").frame(width: 16) }
                .buttonStyle(JarvisIconButtonStyle())
                .help(String(localized: "Navegador: abre páginas junto al chat y pregúntale a Jarvis sobre ellas", bundle: .module))
                .accessibilityLabel(showBrowser ? String(localized: "Cerrar navegador", bundle: .module) : String(localized: "Navegador", bundle: .module))
            Button { showSideChat.toggle() } label: { Image(systemName: "sidebar.right").frame(width: 16) }
                .buttonStyle(JarvisIconButtonStyle())
                .disabled(!model.canManageChatHistory)
                .help(String(localized: "Chat lateral: una pregunta rápida sin interrumpir esta conversación", bundle: .module))
                .accessibilityLabel(showSideChat ? String(localized: "Ocultar chat lateral", bundle: .module) : String(localized: "Chat lateral", bundle: .module))
            Button { Task { await model.toggleMeetingNotes() } } label: {
                if model.meetingRecording, let started = model.meetingStartedAt {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let seconds = max(0, Int(context.date.timeIntervalSince(started)))
                        Label(String(format: String(localized: "Detener %d:%02d", bundle: .module), seconds / 60, seconds % 60),
                              systemImage: "stop.circle.fill")
                            .foregroundStyle(JarvisTheme.failure).monospacedDigit().fixedSize()
                    }
                } else {
                    Image(systemName: "record.circle").frame(width: 16)
                }
            }
                .buttonStyle(JarvisIconButtonStyle())
                .disabled(!model.meetingRecording && (!model.canManageChatHistory || chat.isBusy))
                .help(model.meetingRecording
                      ? String(localized: "Terminar y resumir la reunión", bundle: .module)
                      : String(localized: "Tomar notas de una reunión: transcribe en este Mac y luego la resume. Avisa a los presentes que estás grabando.", bundle: .module))
                .accessibilityLabel(model.meetingRecording ? String(localized: "Terminar notas de reunión", bundle: .module) : String(localized: "Notas de reunión", bundle: .module))
            Button {
                chat.newTemporaryConversation()
                composerFocused = true
            } label: { Image(systemName: "eye.slash").frame(width: 16) }
                .buttonStyle(JarvisIconButtonStyle())
                .disabled(!model.canManageChatHistory || chat.temporary)
                .help(String(localized: "Chat temporal: no se guarda en el historial ni usa ni crea recuerdos", bundle: .module))
                .accessibilityLabel(String(localized: "Chat temporal", bundle: .module))
            // The sidebar already offers "Nuevo chat"; the toolbar keeps it only without one.
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
                    VStack(spacing: 12) {
                        Circle().fill(JarvisTheme.clay).frame(width: 14, height: 14).accessibilityHidden(true)
                        Text(String(localized: "¿En qué te ayudo?", bundle: .module)).font(JarvisTheme.voice(28)).foregroundStyle(JarvisTheme.ink)
                        Text(String(localized: "Escribe una pregunta o una tarea.\nLas acciones que requieran validación te pedirán aprobación.", bundle: .module))
                            .font(.system(size: 13)).foregroundStyle(JarvisTheme.inkSecondary).multilineTextAlignment(.center)
                    }.frame(maxWidth: .infinity).containerRelativeFrame(.vertical).padding(.horizontal, 24)
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

    /// The owner's turns sit in a bone bubble on the right; Jarvis's replies read as
    /// plain serif text on the canvas, with a quiet copy action underneath.
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

    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !chat.attachments.isEmpty { JarvisChatAttachmentsView(chat: chat) }
            VStack(alignment: .leading, spacing: 6) {
                ZStack(alignment: .topLeading) {
                    if draft.isEmpty { Text(attachmentDropTarget ? String(localized: "Suelta aquí tus archivos…", bundle: .module) : String(localized: "Pregúntale a Jarvis…", bundle: .module))
                        .font(.system(size: 14)).foregroundStyle(JarvisTheme.inkMuted).padding(.top, 8).padding(.leading, 5) }
                    TextEditor(text: Binding(get: { draft }, set: { draft = $0 }))
                        .font(.system(size: 14)).scrollContentBackground(.hidden)
                        .frame(minHeight: 40, maxHeight: 120).fixedSize(horizontal: false, vertical: true)
                        .focused($composerFocused)
                        .accessibilityLabel(String(localized: "Mensaje para Jarvis", bundle: .module))
                }
                HStack {
                    MenuBarIconButton(symbol: "paperclip", title: String(localized: "Adjuntar archivos", bundle: .module)) { selectingFiles = true }
                        .disabled(!model.canManageChatHistory)
                        .help(String(localized: "Adjuntar texto, PDF o imagen", bundle: .module)).accessibilityLabel(String(localized: "Adjuntar archivos", bundle: .module))
                    Button { chat.planMode.toggle() } label: {
                        Label(String(localized: "Plan", bundle: .module), systemImage: "list.bullet.clipboard").font(.system(size: 12, weight: .medium))
                            .foregroundStyle(chat.planMode ? JarvisTheme.clay : JarvisTheme.inkSecondary)
                            .padding(.horizontal, 8).frame(height: 26)
                            .background(chat.planMode ? JarvisTheme.clay.opacity(0.13) : .clear, in: Capsule())
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .help(String(localized: "Modo plan: Jarvis investiga y propone un plan sin actuar", bundle: .module))
                    .accessibilityLabel(String(localized: "Modo plan", bundle: .module)).accessibilityValue(chat.planMode ? String(localized: "Activado", bundle: .module) : String(localized: "Desactivado", bundle: .module))
                    if chat.importing { ProgressView().controlSize(.small).accessibilityLabel(String(localized: "Preparando archivos", bundle: .module)) }
                    Text(draft.utf8.count > 4_096 ? String(localized: "El mensaje supera el límite de tamaño.", bundle: .module)
                         : chat.planMode ? String(localized: "Jarvis propondrá un plan sin actuar", bundle: .module) : String(localized: "⌘↩ para enviar", bundle: .module))
                        .font(.system(size: 11)).foregroundStyle(draft.utf8.count > 4_096
                                                                ? HUDStyle.accent(for: .warning) : JarvisInterfaceStyle.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    if chat.transcript.jobID != nil {
                        if validDraft {
                            // While Jarvis works: send after it finishes, or correct it now.
                            Button {
                                if model.queueChatMessage(draft) { draft = "" }
                            } label: { Label(String(localized: "En cola", bundle: .module), systemImage: "text.badge.plus") }
                                .buttonStyle(MenuBarSmallButtonStyle())
                                .disabled(!model.canQueueChatMessage)
                                .help(String(localized: "Se enviará cuando Jarvis termine la respuesta actual", bundle: .module))
                                .keyboardShortcut(.return, modifiers: .command)
                            Button {
                                let correction = draft
                                let conversationID = chat.conversationID
                                let revision = chat.transcriptRevision
                                Task {
                                    guard chat.conversationID == conversationID,
                                          chat.transcriptRevision == revision else { return }
                                    if !(await model.steerChatResponse(correction)) {
                                        chat.restoreRejectedComposer(correction,
                                            conversationID: conversationID, revision: revision)
                                    }
                                }
                            } label: { Label(String(localized: "Corregir", bundle: .module), systemImage: "arrow.uturn.right") }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(chat.cancelling)
                                .help(String(localized: "Detiene la respuesta actual y la repite con tu corrección", bundle: .module))
                        }
                        Button { Task { await model.cancelChatResponse() } } label: {
                            Label(String(localized: "Detener", bundle: .module), systemImage: "stop.fill")
                        }.buttonStyle(MenuBarSmallButtonStyle()).disabled(chat.cancelling)
                    } else {
                        Button {
                            let text = draft
                            let conversationID = chat.conversationID
                            let revision = chat.transcriptRevision
                            Task {
                                guard chat.conversationID == conversationID,
                                      chat.transcriptRevision == revision else { return }
                                let accepted = await model.sendChatMessage(text, consumingComposer: true)
                                if !accepted && model.canSendChatMessage {
                                    chat.restoreRejectedComposer(text,
                                        conversationID: conversationID, revision: revision)
                                }
                            }
                        } label: {
                            Image(systemName: "arrow.up").font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(JarvisTheme.canvas)
                                .frame(width: 28, height: 28)
                                .background(validDraft && model.canSendChatMessage ? JarvisTheme.ink : JarvisTheme.inkMuted,
                                            in: Circle())
                        }
                            .buttonStyle(.plain).accessibilityLabel(String(localized: "Enviar", bundle: .module)).help(String(localized: "Enviar (⌘↩)", bundle: .module))
                            .keyboardShortcut(.return, modifiers: .command)
                            .disabled(!validDraft || !model.canSendChatMessage)
                    }
                }
            }
            .padding(.horizontal, 12).padding(.top, 8).padding(.bottom, 10)
            .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusComposer, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusComposer, style: .continuous)
                .strokeBorder(attachmentDropTarget || composerFocused ? JarvisTheme.hairlineStrong
                              : contrast == .increased ? JarvisTheme.hairlineStrong : JarvisTheme.hairline)
                .allowsHitTesting(false))
            .shadow(color: .black.opacity(0.05), radius: 10, y: 3)
            .dropDestination(for: URL.self) { urls, _ in
                guard model.canManageChatHistory else { return false }
                Task { await chat.addFiles(urls) }
                return true
            } isTargeted: { attachmentDropTarget = $0 }
            if !chat.attachments.isEmpty {
                Text(chat.transcript.jobID != nil
                     ? String(localized: "Envía los adjuntos cuando termine la respuesta actual.", bundle: .module)
                     : String(localized: "Al enviar, NVIDIA recibe el contenido de los adjuntos. Los documentos quedan en este chat; las imágenes se usan en este turno.", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
            }
        }.padding(.horizontal, 20).padding(.bottom, 16).padding(.top, 6)
        .frame(maxWidth: 748).frame(maxWidth: .infinity)
    }
}
