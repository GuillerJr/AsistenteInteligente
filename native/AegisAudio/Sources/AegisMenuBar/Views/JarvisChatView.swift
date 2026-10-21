import AegisAudioCore
import AppKit
import SwiftUI

struct JarvisChatView: View {
    let model: MenuBarModel
    @State private var draft = ""
    @State private var selectingFiles = false
    @State private var attachmentDropTarget = false
    @State private var renameEntry: ChatConversationSummary?
    @State private var showSideChat = false
    @State private var sideChat = SideChatStore()
    @State private var showPalette = false
    @State private var showBrowser = false
    @State private var browser = JarvisBrowserStore()
    @FocusState private var composerFocused: Bool
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    private var chat: JarvisChatStore { model.chat }
    private var accent: Color { HUDStyle.accent(for: .active) }
    private var validDraft: Bool {
        (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !chat.attachments.isEmpty)
            && draft.utf8.count <= 4_096
    }

    var body: some View {
        HStack(spacing: 0) {
            if chat.showHistory {
                JarvisChatHistoryView(model: model).frame(minWidth: 250, idealWidth: 270, maxWidth: 290)
                    .layoutPriority(1)
                Divider()
            }
            switch chat.pane {
            case .chat: chatPanel
            case .dot: JarvisDotView(model: model)
            case .scheduled: JarvisScheduledView(model: model)
            case .project(let id): JarvisProjectView(model: model, projectID: id)
            }
            if showBrowser && chat.pane == .chat {
                Divider()
                JarvisBrowserPanel(store: browser, chat: chat) { showBrowser = false }
            }
            if showSideChat && chat.pane == .chat {
                Divider()
                SideChatPanel(model: model, store: sideChat) { showSideChat = false }
            }
        }
        .frame(minWidth: chat.showHistory ? 740 : 480, idealWidth: 940, minHeight: 460, idealHeight: 720)
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(JarvisTheme.ink)
        .tint(accent)
        .onChange(of: chat.conversationID) { _, _ in draft = ""; composerFocused = true }
        .onChange(of: chat.abandonedTemporaryChats) { _, pending in
            guard !pending.isEmpty else { return }
            Task { await chat.deleteAbandonedTemporaryChats(model: model) }
        }
        .task {
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
            Button("Paleta de comandos") { showPalette.toggle() }
                .keyboardShortcut("k", modifiers: .command).hidden().accessibilityHidden(true)
        }
    }

    private var paletteCommands: [JarvisPaletteCommand] {
        let manage = model.canManageChatHistory
        var commands = [
            JarvisPaletteCommand(id: "new", title: "Nueva conversación", symbol: "square.and.pencil",
                                 shortcut: "⌘N", keywords: ["chat", "nuevo"], enabled: manage) {
                chat.newConversation(); draft = ""; composerFocused = true
            },
            JarvisPaletteCommand(id: "temporary", title: "Chat temporal", symbol: "eye.slash",
                                 keywords: ["privado", "incognito", "sin memoria"],
                                 enabled: manage && !chat.temporary) {
                chat.newTemporaryConversation(); draft = ""; composerFocused = true
            },
            JarvisPaletteCommand(id: "browser", title: showBrowser ? "Cerrar navegador" : "Abrir navegador",
                                 symbol: "globe", keywords: ["web", "pagina", "internet", "url"],
                                 enabled: true) { showBrowser.toggle(); chat.pane = .chat },
            JarvisPaletteCommand(id: "dot", title: "Abrir Dot", symbol: "circle.fill",
                                 keywords: ["autonomo", "actividad", "24/7", "pausar"],
                                 enabled: true) { chat.pane = .dot },
            JarvisPaletteCommand(id: "scheduled", title: "Tareas programadas", symbol: "clock",
                                 keywords: ["programar", "automatizacion", "recordatorio"],
                                 enabled: true) { chat.pane = .scheduled },
            JarvisPaletteCommand(id: "plan", title: chat.planMode ? "Salir del modo plan" : "Modo plan",
                                 symbol: "list.bullet.clipboard", keywords: ["planear", "pasos", "plan"],
                                 enabled: true) { chat.planMode.toggle(); composerFocused = true },
            JarvisPaletteCommand(id: "side", title: showSideChat ? "Cerrar chat lateral" : "Chat lateral",
                                 symbol: "sidebar.right", keywords: ["pregunta", "rapida", "lado"],
                                 enabled: manage) { showSideChat.toggle() },
            JarvisPaletteCommand(id: "history", title: chat.showHistory ? "Ocultar historial" : "Mostrar historial",
                                 symbol: "sidebar.left", keywords: ["conversaciones", "buscar"]) {
                chat.showHistory.toggle()
            },
            JarvisPaletteCommand(id: "pending", title: "Ver lo que requiere tu atención",
                                 symbol: "exclamationmark.bubble",
                                 keywords: ["pendientes", "aprobaciones", "atencion"]) {
                chat.showHistory = true
                Task { await chat.refreshAttention(model: model) }
            },
            JarvisPaletteCommand(id: "meeting",
                                 title: model.meetingRecording ? "Terminar notas de reunión" : "Tomar notas de una reunión",
                                 symbol: model.meetingRecording ? "stop.circle" : "record.circle",
                                 keywords: ["grabar", "reunion", "transcribir"],
                                 enabled: model.meetingRecording || (manage && !chat.isBusy)) {
                Task { await model.toggleMeetingNotes() }
            },
            JarvisPaletteCommand(id: "voice", title: "Hablar con Jarvis", symbol: "mic",
                                 keywords: ["voz", "escuchar", "dictar"]) {
                Task { await model.startVoiceTurn() }
            },
            JarvisPaletteCommand(id: "attach", title: "Adjuntar archivos", symbol: "paperclip",
                                 keywords: ["documento", "pdf", "imagen"], enabled: !chat.isBusy) {
                selectingFiles = true
            },
            JarvisPaletteCommand(id: "quick", title: "Pregunta rápida desde cualquier app", symbol: "sparkles",
                                 shortcut: "⌃⇧J", keywords: ["mini", "flotante"]) {
                QuickEntryPanelController.shared.toggle(model: model)
            },
            JarvisPaletteCommand(id: "settings", title: "Abrir ajustes", symbol: "gearshape", shortcut: "⌘,",
                                 keywords: ["configuracion", "salud", "diagnostico", "integraciones"]) {
                RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
            },
        ]
        if let entry = chat.currentConversation {
            commands.insert(JarvisPaletteCommand(id: "rename", title: "Renombrar este chat", symbol: "pencil",
                                                 keywords: ["titulo", "nombre"], enabled: manage) {
                renameEntry = entry
            }, at: 2)
        }
        if chat.isBusy {
            commands.insert(JarvisPaletteCommand(id: "cancel", title: "Detener la respuesta", symbol: "stop.fill",
                                                 keywords: ["cancelar", "parar"]) {
                Task { await model.cancelChatResponse() }
            }, at: 0)
        }
        return commands
    }

    private var chatPanel: some View {
        VStack(spacing: 0) {
            header
            JarvisSeparator()
            transcript
            if model.interactionID != nil {
                Button {
                    RequestedWindowFocus.open("approval") { openWindow(id: "approval") }
                } label: {
                    Label(model.pendingApproval != nil ? "Revisar acción pendiente" : "Revisar solicitud",
                          systemImage: "exclamationmark.bubble")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .warning)))
                .padding(.horizontal, 20).padding(.bottom, 12)
            }
            if chat.transcript.hasNewerHistory {
                HStack(spacing: 12) {
                    Label("Viendo mensajes anteriores", systemImage: "clock.arrow.circlepath")
                        .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    Spacer()
                    Button("Volver a recientes") { Task { await chat.loadRecent(model: model) } }
                        .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                }.padding(.horizontal, 20).padding(.top, 12)
            }
            if let notice = chat.notice {
                HStack(alignment: .top, spacing: 8) {
                    Label(notice, systemImage: "checkmark.circle").font(.system(size: 12))
                    Spacer()
                    Button { chat.notice = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel("Cerrar confirmación")
                }.foregroundStyle(accent).padding(.horizontal, 20).padding(.top, 12)
            }
            if let error = chat.error {
                HStack(alignment: .top, spacing: 8) {
                    Label(error, systemImage: "exclamationmark.triangle").font(.system(size: 12))
                    Spacer()
                    Button { chat.error = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel("Cerrar aviso")
                }.foregroundStyle(HUDStyle.accent(for: .warning)).padding(.horizontal, 20).padding(.top, 12)
            }
            composer
        }
        .frame(minWidth: 480)
        // Like Codex, web links in replies open in the built-in browser.
        .environment(\.openURL, OpenURLAction { url in
            guard ["http", "https"].contains(url.scheme?.lowercased()) else { return .systemAction }
            browser.open(url)
            showBrowser = true
            return .handled
        })
    }

    private var header: some View {
        HStack(spacing: 12) {
            MenuBarIconButton(symbol: "sidebar.left", title: chat.showHistory ? "Ocultar historial" : "Mostrar historial") {
                chat.showHistory.toggle()
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
                        }.buttonStyle(.plain).help("Este chat usa las instrucciones y archivos del proyecto")
                    }
                }
                Text(chat.temporary ? "Chat temporal · no se guarda ni usa tu memoria"
                     : chat.selectionLoading ? "Cargando conversación" : chat.isBusy ? "\(model.pendingApproval != nil ? "Esperando tu aprobación" : "Preparando respuesta")"
                     : model.canSendChatMessage ? "Listo para conversar" : "\(model.presentationStatus.title)")
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
                    .help("Acciones del chat").accessibilityLabel("Acciones del chat")
            }
            Button { showBrowser.toggle() } label: { Image(systemName: "globe").frame(width: 16) }
                .buttonStyle(JarvisIconButtonStyle())
                .help("Navegador: abre páginas junto al chat y pregúntale a Jarvis sobre ellas")
                .accessibilityLabel(showBrowser ? "Cerrar navegador" : "Navegador")
            Button { showSideChat.toggle() } label: { Image(systemName: "sidebar.right").frame(width: 16) }
                .buttonStyle(JarvisIconButtonStyle())
                .disabled(!model.canManageChatHistory)
                .help("Chat lateral: una pregunta rápida sin interrumpir esta conversación")
                .accessibilityLabel(showSideChat ? "Ocultar chat lateral" : "Chat lateral")
            Button { Task { await model.toggleMeetingNotes() } } label: {
                if model.meetingRecording, let started = model.meetingStartedAt {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let seconds = max(0, Int(context.date.timeIntervalSince(started)))
                        Label(String(format: "Detener %d:%02d", seconds / 60, seconds % 60),
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
                      ? "Terminar y resumir la reunión"
                      : "Tomar notas de una reunión: transcribe en este Mac y luego la resume. Avisa a los presentes que estás grabando.")
                .accessibilityLabel(model.meetingRecording ? "Terminar notas de reunión" : "Notas de reunión")
            Button {
                chat.newTemporaryConversation()
                draft = ""
                composerFocused = true
            } label: { Image(systemName: "eye.slash").frame(width: 16) }
                .buttonStyle(JarvisIconButtonStyle())
                .disabled(!model.canManageChatHistory || chat.temporary)
                .help("Chat temporal: no se guarda en el historial ni usa ni crea recuerdos")
                .accessibilityLabel("Chat temporal")
            // The sidebar already offers "Nuevo chat"; the toolbar keeps it only without one.
            if !chat.showHistory { newConversationButton(compact: true) }

        }.padding(.horizontal, 14).frame(height: 52)
    }

    private func newConversationButton(compact: Bool) -> some View {
        Button {
            chat.newConversation()
            draft = ""
            composerFocused = true
        } label: {
            if compact { Image(systemName: "square.and.pencil").frame(width: 16) }
            else { Label("Nueva conversación", systemImage: "square.and.pencil").fixedSize() }
        }
        .buttonStyle(JarvisIconButtonStyle())
        .disabled(!model.canManageChatHistory)
        .help("Empezar otra conversación con contexto independiente")
        .accessibilityLabel("Nueva conversación")
        .keyboardShortcut("n", modifiers: .command)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if chat.transcript.messages.isEmpty {
                    VStack(spacing: 12) {
                        Circle().fill(JarvisTheme.clay).frame(width: 14, height: 14).accessibilityHidden(true)
                        Text("¿En qué te ayudo?").font(JarvisTheme.voice(28)).foregroundStyle(JarvisTheme.ink)
                        Text("Escribe una pregunta o una tarea.\nLas acciones que requieran validación te pedirán aprobación.")
                            .font(.system(size: 13)).foregroundStyle(JarvisTheme.inkSecondary).multilineTextAlignment(.center)
                    }.frame(maxWidth: .infinity).containerRelativeFrame(.vertical).padding(.horizontal, 24)
                } else {
                    LazyVStack(spacing: 22) {
                        if chat.nextBeforeSequence != nil {
                            Button("Cargar mensajes anteriores") { Task { await chat.loadOlder(model: model) } }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                        }
                        ForEach(chat.transcript.messages) { message in messageView(message) }
                        Color.clear.frame(height: 1).id("latest")
                    }.frame(maxWidth: 700).frame(maxWidth: .infinity).padding(.horizontal, 24).padding(.vertical, 20)
                }
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: chat.transcript.messages.last?.id) { _, _ in
                if !chat.transcript.hasNewerHistory { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .onChange(of: chat.conversationID) { _, _ in proxy.scrollTo("latest", anchor: .bottom) }
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
                    ProgressView().controlSize(.small).accessibilityLabel("Jarvis está preparando la respuesta")
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
                            Task { await model.sendChatMessage("Implementa este plan:\n" + numbered.joined(separator: "\n")) }
                        }
                    }
                }
                if message.interrupted {
                    Label("Solicitud detenida", systemImage: "stop.circle")
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                }
                if message.truncated {
                    Text("Este mensaje se muestra parcialmente.").font(.system(size: 11))
                        .foregroundStyle(JarvisTheme.inkSecondary)
                }
                if !message.text.isEmpty && !fromOwner {
                    MenuBarIconButton(symbol: "doc.on.doc", title: "Copiar mensaje") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(message.text, forType: .string)
                    }
                    .padding(.leading, -6)
                }
            }
            .frame(maxWidth: fromOwner ? 520 : .infinity, alignment: fromOwner ? .trailing : .leading)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(fromOwner ? "Tú" : "Jarvis")
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !chat.attachments.isEmpty { JarvisChatAttachmentsView(chat: chat) }
            VStack(alignment: .leading, spacing: 6) {
                ZStack(alignment: .topLeading) {
                    if draft.isEmpty { Text(attachmentDropTarget ? "Suelta aquí tus archivos…" : "Pregúntale a Jarvis…")
                        .font(.system(size: 14)).foregroundStyle(JarvisTheme.inkMuted).padding(.top, 8).padding(.leading, 5) }
                    TextEditor(text: $draft).font(.system(size: 14)).scrollContentBackground(.hidden)
                        .frame(minHeight: 40, maxHeight: 120).fixedSize(horizontal: false, vertical: true)
                        .focused($composerFocused)
                        .accessibilityLabel("Mensaje para Jarvis")
                }
                HStack {
                    MenuBarIconButton(symbol: "paperclip", title: "Adjuntar archivos") { selectingFiles = true }
                        .disabled(!model.canManageChatHistory)
                        .help("Adjuntar texto, PDF o imagen").accessibilityLabel("Adjuntar archivos")
                    Button { chat.planMode.toggle() } label: {
                        Label("Plan", systemImage: "list.bullet.clipboard").font(.system(size: 12, weight: .medium))
                            .foregroundStyle(chat.planMode ? JarvisTheme.clay : JarvisTheme.inkSecondary)
                            .padding(.horizontal, 8).frame(height: 26)
                            .background(chat.planMode ? JarvisTheme.clay.opacity(0.13) : .clear, in: Capsule())
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .help("Modo plan: Jarvis investiga y propone un plan sin actuar")
                    .accessibilityLabel("Modo plan").accessibilityValue(chat.planMode ? "Activado" : "Desactivado")
                    if chat.importing { ProgressView().controlSize(.small).accessibilityLabel("Preparando archivos") }
                    Text(draft.utf8.count > 4_096 ? "El mensaje supera el límite de tamaño."
                         : chat.planMode ? "Jarvis propondrá un plan sin actuar" : "⌘↩ para enviar")
                        .font(.system(size: 11)).foregroundStyle(draft.utf8.count > 4_096
                                                                ? HUDStyle.accent(for: .warning) : JarvisInterfaceStyle.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    if chat.transcript.jobID != nil {
                        if validDraft {
                            // While Jarvis works: send after it finishes, or correct it now.
                            Button {
                                if model.queueChatMessage(draft) { draft = "" }
                            } label: { Label("En cola", systemImage: "text.badge.plus") }
                                .buttonStyle(MenuBarSmallButtonStyle())
                                .disabled(!model.canQueueChatMessage)
                                .help("Se enviará cuando Jarvis termine la respuesta actual")
                                .keyboardShortcut(.return, modifiers: .command)
                            Button {
                                let correction = draft
                                draft = ""
                                Task {
                                    if !(await model.steerChatResponse(correction)) && draft.isEmpty {
                                        draft = correction
                                    }
                                }
                            } label: { Label("Corregir", systemImage: "arrow.uturn.right") }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(chat.cancelling)
                                .help("Detiene la respuesta actual y la repite con tu corrección")
                        }
                        Button { Task { await model.cancelChatResponse() } } label: {
                            Label("Detener", systemImage: "stop.fill")
                        }.buttonStyle(MenuBarSmallButtonStyle()).disabled(chat.cancelling)
                    } else {
                        Button {
                            let text = draft
                            let conversationID = chat.conversationID
                            let revision = chat.transcriptRevision
                            draft = ""
                            Task {
                                guard chat.conversationID == conversationID,
                                      chat.transcriptRevision == revision else { return }
                                let accepted = await model.sendChatMessage(text)
                                if !accepted && draft.isEmpty && chat.conversationID == conversationID
                                    && chat.transcriptRevision == revision && model.canSendChatMessage {
                                    draft = text
                                }
                            }
                        } label: {
                            Image(systemName: "arrow.up").font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(JarvisTheme.canvas)
                                .frame(width: 28, height: 28)
                                .background(validDraft && model.canSendChatMessage ? JarvisTheme.ink : JarvisTheme.inkMuted,
                                            in: Circle())
                        }
                            .buttonStyle(.plain).accessibilityLabel("Enviar").help("Enviar (⌘↩)")
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
                Text("Al enviar, NVIDIA recibe el contenido de los adjuntos. Los documentos quedan en este chat; las imágenes se usan en este turno.")
                    .font(.system(size: 11)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
            }
        }.padding(.horizontal, 20).padding(.bottom, 16).padding(.top, 6)
        .frame(maxWidth: 748).frame(maxWidth: .infinity)
    }
}
