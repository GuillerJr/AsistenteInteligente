import AegisAudioCore
import AppKit
import SwiftUI

struct JarvisChatView: View {
    let model: MenuBarModel
    @State private var draft = ""
    @State private var selectingFiles = false
    @State private var attachmentDropTarget = false
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
                JarvisChatHistoryView(model: model).frame(width: 240)
                Divider()
            }
            chatPanel
        }
        .frame(minWidth: chat.showHistory ? 740 : 480, idealWidth: 940, minHeight: 460, idealHeight: 720)
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(.white.opacity(0.95))
        .preferredColorScheme(.dark)
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
            Button { chat.showHistory.toggle() } label: { Image(systemName: "sidebar.left") }
                .buttonStyle(.plain).help(chat.showHistory ? "Ocultar historial" : "Mostrar historial")
                .accessibilityLabel(chat.showHistory ? "Ocultar historial" : "Mostrar historial")
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 19)).foregroundStyle(accent)
                .frame(width: 40, height: 40)
                .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text("Chat con Jarvis").font(.system(size: 17, weight: .semibold))
                Text(chat.selectionLoading ? "Cargando conversación" : chat.isBusy ? "\(model.pendingApproval != nil ? "Esperando tu aprobación" : "Preparando respuesta")"
                     : model.canSendChatMessage ? "Listo para conversar" : "\(model.presentationStatus.title)")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                chat.newConversation()
                draft = ""
                composerFocused = true
            } label: { Label("Nueva conversación", systemImage: "square.and.pencil") }
                .buttonStyle(MenuBarSmallButtonStyle())
                .disabled(chat.isEditing || model.pendingInput != nil)
                .help("Empezar otra conversación con contexto independiente")
        }.padding(20)
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
                    }.frame(maxWidth: .infinity).padding(.vertical, 70).padding(.horizontal, 24)
                } else {
                    LazyVStack(spacing: 20) {
                        if chat.nextBeforeSequence != nil {
                            Button("Cargar mensajes anteriores") { Task { await chat.loadOlder(model: model) } }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(!model.canManageChatHistory)
                        }
                        ForEach(chat.transcript.messages) { message in messageView(message) }
                        Color.clear.frame(height: 1).id("latest")
                    }.padding(20)
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
                        Button {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(message.text, forType: .string)
                        } label: { Image(systemName: "doc.on.doc") }
                            .buttonStyle(.plain).help("Copiar mensaje").accessibilityLabel("Copiar mensaje")
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
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
                if message.truncated {
                    Text("Este mensaje se muestra parcialmente.").font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .padding(16).frame(maxWidth: 560, alignment: .leading)
            .background(message.role == .user ? accent.opacity(0.12) : .white.opacity(0.04),
                        in: RoundedRectangle(cornerRadius: 14))
            if message.role == .assistant { Spacer(minLength: 24) }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !chat.attachments.isEmpty { JarvisChatAttachmentsView(chat: chat) }
            ZStack(alignment: .topLeading) {
                if draft.isEmpty { Text(attachmentDropTarget ? "Suelta aquí tus archivos…" : "Escribe a Jarvis…")
                    .foregroundStyle(.secondary).padding(.top, 8).padding(.leading, 5) }
                TextEditor(text: $draft).scrollContentBackground(.hidden)
                    .frame(height: 72).focused($composerFocused)
                    .accessibilityLabel("Mensaje para Jarvis")
            }.padding(8).background(.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(accent.opacity(attachmentDropTarget ? 0.8 : 0), lineWidth: 1))
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
                Text(draft.utf8.count > 4_096 ? "El mensaje supera el límite de tamaño." : "⌘Return para enviar · Historial local")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
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
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }.padding(20)
    }
}
