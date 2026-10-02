import SwiftUI

struct NotchComposer: View {
    let model: MenuBarModel
    @Binding var draft: String
    let keepOpen: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 6) {
            TextField("Escríbele a Jarvis…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain).font(.system(size: 13)).lineLimit(1...2)
                .focused($focused).onSubmit(send)
                .disabled(!model.canSendChatMessage)
                .accessibilityLabel("Mensaje para Jarvis")
            Button(action: send) { Image(systemName: "arrow.up").font(.system(size: 12, weight: .bold)) }
                .buttonStyle(NotchConversationButtonStyle()).disabled(!canSend)
                .accessibilityLabel("Enviar mensaje desde la mascota")
        }.padding(8).background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            .onChange(of: focused) { _, value in if value { keepOpen() } }
            .onChange(of: model.mascotSessionAvailable) { _, value in if !value { focused = false } }
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
