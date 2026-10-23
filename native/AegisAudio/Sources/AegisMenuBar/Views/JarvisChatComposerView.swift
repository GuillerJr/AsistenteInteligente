import AegisAudioCore
import AppKit
import SwiftUI

struct JarvisChatComposerView: View {
    let model: MenuBarModel
    var composerFocused: FocusState<Bool>.Binding
    var interactive = true
    var onAttach: () -> Void
    @State private var attachmentDropTarget = false
    @Environment(\.colorSchemeContrast) private var contrast

    private var chat: JarvisChatStore { model.chat }
    private var draft: String {
        get { chat.composerDraft ?? "" }
        nonmutating set { chat.composerDraft = newValue.isEmpty ? nil : newValue }
    }
    private var validDraft: Bool {
        JarvisComposerAvailability.validDraft(draft, hasAttachments: !chat.attachments.isEmpty)
    }
    private var submission: JarvisComposerAvailability.Submission? {
        JarvisComposerAvailability.submission(draft, hasAttachments: !chat.attachments.isEmpty,
            running: chat.transcript.jobID != nil, cancelling: chat.cancelling,
            canSend: model.canSendChatMessage, canQueue: model.canQueueChatMessage,
            interactive: interactive)
    }
    private var canSubmit: Bool { submission != nil }
    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: JarvisTheme.radiusComposer, style: .continuous)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 10) {
                if !chat.attachments.isEmpty {
                    JarvisChatAttachmentsView(chat: chat).disabled(!interactive)
                }
                ZStack(alignment: .topLeading) {
                    if draft.isEmpty {
                        Text(attachmentDropTarget
                             ? String(localized: "Suelta aquí tus archivos…", bundle: .module)
                             : JarvisComposerAvailability.placeholder(
                                running: chat.transcript.jobID != nil, planning: chat.planMode,
                                inProject: chat.workspaceProject != nil))
                            .font(.system(size: 14)).foregroundStyle(JarvisTheme.inkMuted)
                            .padding(.top, 5).allowsHitTesting(false).accessibilityHidden(true)
                    }
                    JarvisComposerTextEditor(
                        text: Binding(get: { draft }, set: { draft = $0 }),
                        focused: Binding(get: { composerFocused.wrappedValue },
                                         set: { composerFocused.wrappedValue = $0 }),
                        draftIdentity: chat.conversationID ?? chat.transcriptRevision,
                        interactive: interactive,
                        onSubmit: submit)
                        .frame(height: 76)
                }
                HStack(spacing: 8) {
                    MenuBarIconButton(symbol: "plus", title: String(localized: "Adjuntar archivos", bundle: .module)) {
                        guard interactive, model.canManageChatHistory else { return }
                        onAttach()
                    }
                    .disabled(!interactive || !model.canManageChatHistory)
                    .help(String(localized: "Adjuntar texto, PDF o imagen", bundle: .module))
                    modeMenu
                    if chat.importing {
                        ProgressView().controlSize(.small)
                            .accessibilityLabel(String(localized: "Preparando archivos", bundle: .module))
                    }
                    Spacer(minLength: 4)
                    if chat.transcript.jobID != nil {
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 8) { runningActions }
                            HStack(spacing: 8) { runningActions }.labelStyle(.iconOnly)
                        }
                    } else {
                        MenuBarIconButton(symbol: "waveform", title: String(localized: "Hablar con Jarvis", bundle: .module)) {
                            guard interactive, model.canStartVoiceTurn else { return }
                            Task { await model.startVoiceTurn() }
                        }
                        .disabled(!interactive || !model.canStartVoiceTurn)
                        submitButton
                    }
                }
            }
            .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 12)
            .background(JarvisTheme.raised, in: shape)
            .overlay(shape.strokeBorder(attachmentDropTarget || composerFocused.wrappedValue
                                       || contrast == .increased
                                       ? JarvisTheme.hairlineStrong : JarvisTheme.hairline)
                .allowsHitTesting(false))
            .shadow(color: .black.opacity(0.035), radius: 8, y: 2)
            .dropDestination(for: URL.self) { urls, _ in
                guard interactive, model.canManageChatHistory else { return false }
                Task { await chat.addFiles(urls) }
                return true
            } isTargeted: { attachmentDropTarget = interactive && $0 }
            footer
        }
        .padding(.horizontal, 20).padding(.bottom, 16).padding(.top, 6)
        .frame(maxWidth: 748).frame(maxWidth: .infinity)
    }

    private var modeMenu: some View {
        Menu {
            Button {
                guard interactive else { return }
                chat.planMode = false
            } label: {
                Label(String(localized: "Conversar", bundle: .module),
                      systemImage: chat.planMode ? "bubble.left" : "checkmark")
            }
            Button {
                guard interactive else { return }
                chat.planMode = true
            } label: {
                Label(String(localized: "Plan", bundle: .module),
                      systemImage: chat.planMode ? "checkmark" : "list.bullet.clipboard")
            }
        } label: {
            Label(chat.planMode ? String(localized: "Plan", bundle: .module)
                               : String(localized: "Conversar", bundle: .module),
                  systemImage: chat.planMode ? "list.bullet.clipboard" : "bubble.left")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(chat.planMode ? JarvisTheme.clay : JarvisTheme.inkSecondary)
                .padding(.horizontal, 7).frame(height: 28)
                .background(chat.planMode ? JarvisTheme.clay.opacity(0.10) : JarvisTheme.fill,
                            in: Capsule())
        }
        .menuStyle(.borderlessButton).fixedSize().disabled(!interactive)
        .help(String(localized: "Modo plan: Jarvis investiga y propone un plan sin actuar", bundle: .module))
        .accessibilityLabel(String(localized: "Modo de conversación", bundle: .module))
        .accessibilityValue(chat.planMode ? String(localized: "Plan", bundle: .module)
                                         : String(localized: "Conversar", bundle: .module))
    }

    @ViewBuilder private var runningActions: some View {
        if validDraft {
            Button(action: submit) {
                Label(String(localized: "En cola", bundle: .module), systemImage: "text.badge.plus")
            }
            .buttonStyle(MenuBarSmallButtonStyle()).disabled(!canSubmit)
            .help(String(localized: "Se enviará cuando Jarvis termine la respuesta actual", bundle: .module))
            .keyboardShortcut(.return, modifiers: .command)
            Button(action: correct) {
                Label(String(localized: "Corregir", bundle: .module), systemImage: "arrow.uturn.right")
            }
            .buttonStyle(MenuBarSmallButtonStyle())
            .disabled(!interactive || chat.cancelling || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .help(String(localized: "Detiene la respuesta actual y la repite con tu corrección", bundle: .module))
        }
        Button {
            guard interactive, !chat.cancelling else { return }
            Task { await model.cancelChatResponse() }
        } label: {
            Image(systemName: "stop.fill").font(.system(size: 11, weight: .semibold))
                .foregroundStyle(JarvisTheme.canvas).frame(width: 32, height: 32)
                .background(JarvisTheme.ink, in: Circle())
        }
        .buttonStyle(.plain).disabled(!interactive || chat.cancelling)
        .accessibilityLabel(String(localized: "Detener la respuesta", bundle: .module))
        .help(String(localized: "Detener la respuesta", bundle: .module))
    }

    private var submitButton: some View {
        Button(action: submit) {
            Image(systemName: "arrow.up").font(.system(size: 14, weight: .semibold))
                .foregroundStyle(JarvisTheme.canvas).frame(width: 32, height: 32)
                .background(canSubmit ? JarvisTheme.ink : JarvisTheme.inkMuted, in: Circle())
        }
        .buttonStyle(.plain).disabled(!canSubmit)
        .accessibilityLabel(String(localized: "Enviar", bundle: .module))
        .help(String(localized: "Enviar (Intro o ⌘↩)", bundle: .module))
        .keyboardShortcut(.return, modifiers: .command)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(draft.utf8.count > 4_096
                 ? String(localized: "El mensaje supera el límite de tamaño.", bundle: .module)
                 : chat.planMode
                 ? String(localized: "Jarvis propondrá un plan sin actuar", bundle: .module)
                 : String(localized: "Intro para enviar · Mayús-Intro para una nueva línea", bundle: .module))
                .foregroundStyle(draft.utf8.count > 4_096
                                 ? HUDStyle.accent(for: .warning) : JarvisInterfaceStyle.secondaryText)
            if !chat.attachments.isEmpty {
                Text(chat.transcript.jobID != nil
                     ? String(localized: "Envía los adjuntos cuando termine la respuesta actual.", bundle: .module)
                     : String(localized: "Al enviar, Codex (OpenAI) recibe el contenido de los adjuntos. Los documentos quedan en este chat; las imágenes se usan en este turno.", bundle: .module))
                    .foregroundStyle(JarvisInterfaceStyle.secondaryText)
            }
        }
        .font(.system(size: 11)).fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, 3)
    }

    private func submit() {
        // SwiftUI may resolve ⌘Return as a button equivalent before the native editor
        // sees keyDown. Preserve the same IME protection on that path as well.
        if let editor = NSApp.keyWindow?.firstResponder as? JarvisComposerNativeTextView,
           editor.hasMarkedText() { return }
        guard let submission else { return }
        if submission == .queue {
            if model.queueChatMessage(draft) { draft = "" }
            return
        }
        let text = draft, conversationID = chat.conversationID, revision = chat.transcriptRevision
        Task {
            guard interactive, chat.conversationID == conversationID,
                  chat.transcriptRevision == revision else { return }
            let accepted = await model.sendChatMessage(text, consumingComposer: true)
            if !accepted && model.canSendChatMessage {
                chat.restoreRejectedComposer(text, conversationID: conversationID, revision: revision)
            }
        }
    }

    private func correct() {
        guard interactive, validDraft, !chat.cancelling, chat.transcript.jobID != nil,
              !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let text = draft, conversationID = chat.conversationID, revision = chat.transcriptRevision
        Task {
            guard chat.conversationID == conversationID, chat.transcriptRevision == revision else { return }
            if !(await model.steerChatResponse(text)) {
                chat.restoreRejectedComposer(text, conversationID: conversationID, revision: revision)
            }
        }
    }
}
