import AegisAudioCore
import SwiftUI

/// Conversation lives below the camera; hovering never starts a request.
struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var interactive = true
    var togglePinned: () -> Void = {}
    var keepOpen: () -> Void = {}
    var activateVoice: () -> Void = {}
    var reviewInteraction: () -> Void = {}
    var dismiss: () -> Void = {}
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    @State private var draft = ""
    @State private var dropTargeted = false
    @FocusState private var composerFocused: Bool

    var body: some View {
        let progress = CGFloat(presentation.expansionProgress)
        let mascotSize = 36 + 36 * progress
        let top = 2 + 6 * progress
        let outline = NotchOutline(topInset: 12 * progress, bottomRadius: 12 + 10 * progress)
        let reveal = min(max((presentation.expansionProgress - 0.75) / 0.20, 0), 1)
        let opacity = reveal * reveal * (3 - 2 * reveal)
        GeometryReader { geometry in
            ZStack(alignment: .top) {
                Button(action: togglePinned) {
                    NotchMascotArtwork(model: model,
                                       engaged: presentation.interaction.isHovered || presentation.interaction.isPinned,
                                       reduceMotion: systemReduceMotion || forceReduceMotion)
                        .frame(width: 112, height: 112)
                        .scaleEffect(mascotSize / 112)
                        .frame(width: mascotSize, height: mascotSize)
                        .frame(maxWidth: .infinity)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!interactive)
                .accessibilityLabel("Mascota de Jarvis")
                .accessibilityValue(model.presentationStatus.accessibilityDescription)
                .accessibilityAction(named: Text("Hablar con Jarvis"), activateVoice)
                .help("Pulsa para fijar o soltar. Doble clic para hablar o interrumpir una respuesta hablada.")
                .position(x: geometry.size.width / 2, y: top + mascotSize / 2)

                Text(model.presentationStatus.compactTitle)
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .foregroundStyle(model.presentationStatus.tone.color)
                    .lineLimit(1)
                    .frame(width: max(0, geometry.size.width - 32), height: 18)
                    .position(x: geometry.size.width / 2, y: top + mascotSize + 12)
                    .opacity(opacity).allowsHitTesting(false)
                    .accessibilityHidden(opacity < 0.95)

                conversation
                    .frame(width: max(0, geometry.size.width - 32),
                           height: max(0, geometry.size.height - 120), alignment: .top)
                    .position(x: geometry.size.width / 2, y: 108 + max(0, geometry.size.height - 120) / 2)
                    .opacity(opacity)
                    .disabled(!interactive)
                    .allowsHitTesting(interactive && opacity > 0.95)
                    .accessibilityHidden(opacity < 0.95)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(.black, in: outline)
        .overlay { if dropTargeted { outline.stroke(.cyan.opacity(0.7), lineWidth: 2) } }
        .clipShape(outline).contentShape(outline).clipped().ignoresSafeArea()
        .preferredColorScheme(.dark)
        .onExitCommand(perform: dismiss)
        .onChange(of: composerFocused) { _, focused in if focused { keepOpen() } }
        .onChange(of: model.chat.conversationID) { _, _ in draft = "" }
        .onChange(of: model.mascotSessionAvailable) { _, available in
            if !available { draft = ""; composerFocused = false; dismiss() }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard interactive, model.canManageChatHistory, !urls.isEmpty else { return false }
            keepOpen()
            Task { await model.addMascotFiles(urls) }
            return true
        } isTargeted: { targeted in
            dropTargeted = targeted
            if targeted && interactive && model.canManageChatHistory { keepOpen() }
        }
    }

    private var conversation: some View {
        let content = model.mascotPresentation
        return VStack(alignment: .leading, spacing: 8) {
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    Text(content.title).font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(.cyan)
                    if let understood = model.mascotUnderstoodText, !content.requiresReview {
                        Text("Entendí: \(understood)").font(.system(size: 11))
                            .foregroundStyle(.secondary).lineLimit(2)
                    }
                    JarvisChatResponseView(content.message)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(10)
            }
            .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
            .frame(maxHeight: .infinity)

            if model.mascotSessionAvailable {
                if content.requiresReview {
                    Button("Revisar y responder", action: reviewInteraction)
                        .buttonStyle(NotchConversationButtonStyle())
                        .frame(maxWidth: .infinity, alignment: .trailing)
                        .accessibilityHint("Abre el diálogo de Jarvis. No aprueba ninguna acción.")
                } else {
                    attachmentStrip
                    composer
                    HStack(spacing: 8) {
                        Text(model.chat.conversationID == nil ? "Nueva conversación" : "Chat activo")
                            .font(.system(size: 10)).foregroundStyle(.secondary)
                        Spacer()
                        if model.chat.isBusy {
                            Button { Task { await model.cancelChatResponse() } } label: {
                                Label("Detener", systemImage: "stop.fill")
                            }.disabled(model.chat.cancelling)
                        } else {
                            Button(action: activateVoice) {
                                Label(model.voiceState == .speaking || model.voiceState == .processing
                                      ? "Interrumpir" : "Hablar", systemImage: "waveform")
                            }.disabled(!model.canActivateMascotVoice)
                        }
                    }.buttonStyle(NotchConversationButtonStyle())
                }
            }
        }
    }

    private var composer: some View {
        HStack(alignment: .center, spacing: 6) {
            TextField("Escríbele a Jarvis…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain).font(.system(size: 13)).lineLimit(1...2)
                .focused($composerFocused).onSubmit(send)
                .disabled(!model.canSendChatMessage)
                .accessibilityLabel("Mensaje para Jarvis")
            Button(action: send) { Image(systemName: "arrow.up").font(.system(size: 12, weight: .bold)) }
                .buttonStyle(NotchConversationButtonStyle()).disabled(!canSend)
                .accessibilityLabel("Enviar mensaje desde la mascota")
        }.padding(8).background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private var attachmentStrip: some View {
        if model.chat.importing {
            Text("Preparando los archivos…").font(.system(size: 10)).foregroundStyle(.secondary)
        } else if !model.chat.attachments.isEmpty {
            ScrollView(.horizontal) {
                HStack(spacing: 6) {
                    ForEach(model.chat.attachments) { attachment in
                        Button {
                            model.chat.attachments.removeAll { $0.id == attachment.id }
                        } label: {
                            Label(attachment.name, systemImage: "xmark.circle.fill").lineLimit(1)
                        }.disabled(!model.canManageChatHistory)
                            .accessibilityLabel("Quitar adjunto \(attachment.name)")
                    }
                }.buttonStyle(NotchConversationButtonStyle())
            }.scrollIndicators(.hidden).frame(height: 26)
        }
    }

    private var canSend: Bool {
        model.canSendChatMessage && draft.utf8.count <= 4_096
            && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.chat.attachments.isEmpty)
    }

    private func send() {
        guard canSend else { return }
        let text = draft
        draft = ""
        keepOpen()
        Task { await model.sendChatMessage(text) }
    }
}

/// Audio metering redraws the artwork, not the Markdown conversation or input.
private struct NotchMascotArtwork: View {
    let model: MenuBarModel
    let engaged: Bool
    let reduceMotion: Bool
    var body: some View {
        JarvisNotchMascot(mood: model.mascotNotice != nil && !model.voiceState.isBusy
                         && model.voiceState != .speaking ? .attention : .init(kind: model.presentationStatus.kind),
                         energy: Double(model.voiceActivityLevel), engaged: engaged,
                         forceReduceMotion: reduceMotion)
    }
}

private struct NotchConversationButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 11, weight: .medium, design: .rounded))
            .foregroundStyle(enabled ? Color.cyan : .gray)
            .padding(.horizontal, 9).padding(.vertical, 6)
            .background(.cyan.opacity(enabled ? (configuration.isPressed ? 0.22 : 0.10) : 0.04),
                        in: RoundedRectangle(cornerRadius: 8))
            .contentShape(RoundedRectangle(cornerRadius: 8))
    }
}
