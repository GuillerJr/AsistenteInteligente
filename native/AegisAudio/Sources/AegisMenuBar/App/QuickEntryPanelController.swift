import AegisAudioCore
import AppKit
import SwiftUI

/// ⌃⇧J from anywhere: a small floating box to ask Jarvis something in writing
/// (Claude's Quick Entry, Codex's "Mini"). Each question starts a new chat, the reply
/// streams in the box, and the conversation stays in the chat history.
@MainActor
final class QuickEntryPanelController {
    static let shared = QuickEntryPanelController()

    private var panel: QuickEntryPanel?

    private init() {}

    func toggle(model: MenuBarModel) {
        if let panel, panel.isVisible {
            close()
            return
        }
        let panel = self.panel ?? makePanel(model: model)
        self.panel = panel
        if let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) })
            ?? NSScreen.main {
            let size = panel.frame.size
            let visible = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: visible.midX - size.width / 2,
                                         y: visible.maxY - size.height - visible.height * 0.18))
        }
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
    }

    func close() {
        panel?.orderOut(nil)
    }

    private func makePanel(model: MenuBarModel) -> QuickEntryPanel {
        let panel = QuickEntryPanel(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 240),
            styleMask: [.titled, .fullSizeContentView, .closable],
            backing: .buffered, defer: false
        )
        panel.title = "Jarvis · Pregunta rápida"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isMovableByWindowBackground = true
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.contentView = NSHostingView(rootView: QuickEntryView(model: model) { [weak self] in
            self?.close()
        })
        return panel
    }
}

private final class QuickEntryPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { orderOut(nil) }
}

private struct QuickEntryView: View {
    let model: MenuBarModel
    let close: () -> Void
    @State private var question = ""
    @State private var asked: String?
    @FocusState private var focused: Bool

    private var reply: String? {
        guard asked != nil,
              let last = model.chat.transcript.messages.last, last.role == .assistant
        else { return nil }
        return last.text.isEmpty ? nil : last.text
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "sparkles").foregroundStyle(.secondary)
                TextField("Pregúntale a Jarvis…", text: $question)
                    .textFieldStyle(.plain).font(.system(size: 17))
                    .focused($focused)
                    .onSubmit(send)
                    .disabled(model.chat.isBusy)
                if model.chat.isBusy { ProgressView().controlSize(.small) }
            }
            if let asked {
                Text(asked).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1)
                ScrollView {
                    Text(reply ?? "Pensando…")
                        .font(.system(size: 13)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 160)
            } else {
                Text("Enter para preguntar · Esc para cerrar · La conversación queda en tu historial.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
        .padding(18)
        .frame(width: 560)
        .onAppear { focused = true }
    }

    private func send() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, model.canSendChatMessage else { return }
        model.chat.newConversation()
        asked = text
        question = ""
        Task {
            if !(await model.sendChatMessage(text)) {
                asked = nil
                question = text
            }
        }
    }
}
