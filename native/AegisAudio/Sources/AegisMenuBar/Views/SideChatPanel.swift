import AegisAudioCore
import SwiftUI

struct SideChatPanel: View {
    let model: MenuBarModel
    let store: SideChatStore
    let close: () -> Void
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Chat lateral").font(.system(size: 13, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                Spacer()
                Button {
                    Task { await store.close { await model.performChatIPC($0, payload: $1) }; close() }
                } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).accessibilityLabel("Cerrar chat lateral")
            }
            .padding(14)
            Text("Pregunta algo rápido sin interrumpir el chat principal. No se guarda.")
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 14)
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(store.lines) { line in
                            Text(line.text.isEmpty ? "Pensando…" : line.text)
                                .font(line.fromOwner ? .system(size: 12.5) : JarvisTheme.voice(13))
                                .foregroundStyle(JarvisTheme.ink).textSelection(.enabled)
                                .padding(.horizontal, line.fromOwner ? 11 : 0).padding(.vertical, line.fromOwner ? 7 : 0)
                                .background(line.fromOwner ? JarvisTheme.userBubble : .clear,
                                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                                .frame(maxWidth: .infinity, alignment: line.fromOwner ? .trailing : .leading)
                                .id(line.id)
                        }
                    }.padding(14)
                }
                .onChange(of: store.lines) { _, lines in
                    if let last = lines.last { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
            Divider()
            HStack(spacing: 8) {
                TextField("Pregunta rápida…", text: $draft).jarvisInput(focused: focused, fontSize: 12.5)
                    .focused($focused).onSubmit(send).disabled(store.busy)
                if store.busy { ProgressView().controlSize(.small) }
            }
            .padding(14)
        }
        .frame(minWidth: 260, idealWidth: 300, maxWidth: 340)
        .background(JarvisTheme.sidebar)
        .onAppear { focused = true }
    }

    private func send() {
        let text = draft
        draft = ""
        Task { await store.send(text) { await model.performChatIPC($0, payload: $1) } }
    }
}
