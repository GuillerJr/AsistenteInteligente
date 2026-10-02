import AegisAudioCore
import AppKit
import SwiftUI

struct JarvisChatView: View {
    let model: MenuBarModel
    @State private var draft = ""
    @State private var selectingFiles = false
    @State private var attachmentDropTarget = false
    @State private var renameEntry: ChatConversationSummary?
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
            chatPanel
        }
        .frame(minWidth: chat.showHistory ? 740 : 480, idealWidth: 940, minHeight: 460, idealHeight: 720)
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(Color.primary.opacity(0.95))
        .tint(accent)
        .onChange(of: chat.conversationID) { _, _ in draft = ""; composerFocused = true }
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
    }

    private var chatPanel: some View {
        VStack(spacing: 0) {
            header
            Divider()
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
            Divider()
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
    }

    private var header: some View {
        HStack(spacing: 12) {
            MenuBarIconButton(symbol: "sidebar.left", title: chat.showHistory ? "Ocultar historial" : "Mostrar historial") {
                chat.showHistory.toggle()
            }
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 19)).foregroundStyle(accent)
                .frame(width: 40, height: 40)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text(verbatim: chat.currentTitle).font(.system(size: 17, weight: .semibold))
                    .lineLimit(1).help(chat.currentTitle)
                Text(chat.selectionLoading ? "Cargando conversación" : chat.isBusy ? "\(model.pendingApproval != nil ? "Esperando tu aprobación" : "Preparando respuesta")"
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
            ViewThatFits(in: .horizontal) {
                newConversationButton(compact: false)
                newConversationButton(compact: true)
            }

        }.padding(20)
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
        .buttonStyle(MenuBarSmallButtonStyle())
        .disabled(!model.canManageChatHistory)
        .help("Empezar otra conversación con contexto independiente")
        .accessibilityLabel("Nueva conversación")
        .keyboardShortcut("n", modifiers: .command)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if chat.transcript.messages.isEmpty {
                    VStack(spacing: 14) {
                        Image(systemName: "text.bubble").font(.system(size: 32)).foregroundStyle(accent)
                        Text("¿En qué te ayudo?").font(.system(size: 23, weight: .semibold))
                        Text("Escribe una pregunta o una tarea.\nLas acciones que requieran validación te pedirán aprobación.")
                            .font(.system(size: 13)).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }.frame(maxWidth: .infinity).containerRelativeFrame(.vertical).padding(.horizontal, 24)
                } else {
                    LazyVStack(spacing: 16) {
                        if chat.nextBeforeSequence != nil {
                            Button("Cargar mensajes anteriores") { Task { await chat.loadOlder(model: model) } }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                        }
                        ForEach(chat.transcript.messages) { message in messageView(message) }
                        Color.clear.frame(height: 1).id("latest")
                    }.frame(maxWidth: 820).frame(maxWidth: .infinity).padding(20)
                }
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: chat.transcript.messages.count) { _, _ in
                if !chat.selectionLoading { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .onChange(of: chat.conversationID) { _, _ in proxy.scrollTo("latest", anchor: .bottom) }
        }
    }

    private func messageView(_ message: TextChatTranscript.Message) -> some View {
        HStack(alignment: .top, spacing: 12) {
            if message.role == .user { Spacer(minLength: 40) }
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(message.role == .user ? "Tú" : "Jarvis").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                    Spacer()
                    if !message.text.isEmpty {
                        MenuBarIconButton(symbol: "doc.on.doc", title: "Copiar mensaje") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(message.text, forType: .string)
                        }
                    }
                }
                if !message.attachmentNames.isEmpty {
                    ForEach(message.attachmentNames, id: \.self) { name in
                        Label { Text(verbatim: name).lineLimit(1).truncationMode(.middle) } icon: {
                            Image(systemName: "paperclip")
                        }.font(.system(size: 11)).foregroundStyle(accent)
                    }
                }
                if message.text.isEmpty {
                    ProgressView().controlSize(.small).accessibilityLabel("Jarvis está preparando la respuesta")
                } else {
                    if message.role == .assistant {
                        JarvisChatResponseView(message.text)
                    } else {
                        Text(verbatim: message.text).font(.system(size: 14)).lineSpacing(4)
                            .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if message.interrupted {
                    Label("Solicitud detenida", systemImage: "stop.circle")
                        .font(.system(size: 11)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
                }
                if message.truncated {
                    Text("Este mensaje se muestra parcialmente.").font(.system(size: 11)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
                }
            }
            .padding(18).frame(maxWidth: message.role == .assistant ? 740 : 640, alignment: .leading)
            .background(message.role == .user ? accent.opacity(0.12) : Color.primary.opacity(0.04),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Color.primary.opacity(contrast == .increased ? 0.45 : 0.07)).allowsHitTesting(false))
            if message.role == .assistant { Spacer(minLength: 24) }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !chat.attachments.isEmpty { JarvisChatAttachmentsView(chat: chat) }
            ZStack(alignment: .topLeading) {
                if draft.isEmpty { Text(attachmentDropTarget ? "Suelta aquí tus archivos…" : "Escribe a Jarvis…")
                    .foregroundStyle(.secondary).padding(.top, 8).padding(.leading, 5) }
                TextEditor(text: $draft).font(.system(size: 14)).scrollContentBackground(.hidden)
                    .frame(height: 72).focused($composerFocused)
                    .accessibilityLabel("Mensaje para Jarvis")
            }.padding(8).background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(attachmentDropTarget || composerFocused ? accent.opacity(0.65)
                                  : Color.primary.opacity(contrast == .increased ? 0.45 : 0.14), lineWidth: 1)
                    .allowsHitTesting(false))
                .dropDestination(for: URL.self) { urls, _ in
                    guard model.canManageChatHistory else { return false }
                    Task { await chat.addFiles(urls) }
                    return true
                } isTargeted: { attachmentDropTarget = $0 }
            HStack {
                Button { selectingFiles = true } label: { Image(systemName: "paperclip") }
                    .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                    .help("Adjuntar texto, PDF o imagen").accessibilityLabel("Adjuntar archivos")
                if chat.importing { ProgressView().controlSize(.small).accessibilityLabel("Preparando archivos") }
                Text(draft.utf8.count > 4_096 ? "El mensaje supera el límite de tamaño." : "⌘Return para enviar")
                    .font(.system(size: 11)).foregroundStyle(draft.utf8.count > 4_096
                                                            ? HUDStyle.accent(for: .warning) : JarvisInterfaceStyle.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                if chat.transcript.jobID != nil {
                    Button { Task { await model.cancelChatResponse() } } label: {
                        Label("Detener", systemImage: "stop.fill")
                    }.buttonStyle(MenuBarSmallButtonStyle()).disabled(chat.cancelling)
                } else {
                    Button {
                        let text = draft
                        draft = ""
                        Task { await model.sendChatMessage(text) }
                    } label: { Label("Enviar", systemImage: "arrow.up") }
                        .buttonStyle(HUDActionStyle(accent: accent, prominent: true))
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(!validDraft || !model.canSendChatMessage)
                }
            }
            if !chat.attachments.isEmpty {
                Text("Al enviar, NVIDIA recibe el contenido de los adjuntos. Los documentos quedan en este chat; las imágenes se usan en este turno.")
                    .font(.system(size: 11)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
            }
        }.padding(20)
    }
}
