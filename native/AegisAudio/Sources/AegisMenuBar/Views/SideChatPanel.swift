import AegisAudioCore
import SwiftUI

/// A quick question beside the main chat (Claude's and Codex's side chat). It runs in
/// its own temporary conversation, so it never interrupts the main chat, is not listed
/// in the history, uses and creates no memories, and is deleted when closed. Actions
/// that need an approval belong in the main chat, where the approval window lives.
@MainActor @Observable
final class SideChatStore {
    struct Line: Identifiable, Equatable {
        let id = UUID()
        let fromOwner: Bool
        var text: String
    }

    var lines: [Line] = []
    var busy = false
    private var conversationID: UUID?

    func send(_ text: String, model: MenuBarModel) async {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, question.utf8.count <= 4_096, !busy else { return }
        busy = true
        defer { busy = false }
        lines.append(Line(fromOwner: true, text: question))
        lines.append(Line(fromOwner: false, text: ""))
        let reply = lines.count - 1
        if conversationID == nil,
           let data = await model.performChatIPC("conversations.create", payload: [
               "title": "Chat lateral", "temporary": true,
           ]),
           let created = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let raw = created["conversation_id"] as? String {
            conversationID = UUID(uuidString: raw)
        }
        guard let conversationID,
              let submitted = await model.performChatIPC("swarm.submit", payload: [
                  "text": question, "conversation_id": conversationID.uuidString.lowercased(),
              ]),
              var snapshot = try? JSONSerialization.jsonObject(with: submitted) as? [String: Any],
              let jobID = snapshot["job_id"] as? String
        else {
            lines[reply].text = "No pude enviar la pregunta. Inténtalo de nuevo."
            return
        }
        for _ in 0 ..< 60 {
            let status = snapshot["status"] as? String ?? ""
            if let partial = snapshot["partial_result"] as? String, !partial.isEmpty {
                lines[reply].text = partial
            }
            if ["completed", "failed", "cancelled"].contains(status) {
                lines[reply].text = (snapshot["result"] as? String).flatMap { $0.isEmpty ? nil : $0 }
                    ?? (status == "completed" ? lines[reply].text
                        : "No pude completar la pregunta (\(snapshot["error_code"] as? String ?? status)).")
                return
            }
            if status == "awaiting_confirmation" {
                _ = await model.performChatIPC("jobs.cancel", payload: ["job_id": jobID])
                lines[reply].text = "Eso necesita tu aprobación: pídelo en el chat principal."
                return
            }
            guard let data = await model.performChatIPC("jobs.wait", payload: [
                "job_id": jobID,
                "after_stream_version": snapshot["stream_version"] as? Int ?? 0,
                "timeout_milliseconds": 10_000,
            ]), let next = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                lines[reply].text = "Perdí la conexión con Jarvis."
                return
            }
            snapshot = next
        }
        lines[reply].text = "La respuesta tardó demasiado."
    }

    func close(model: MenuBarModel) async {
        guard let conversationID else { lines = []; return }
        self.conversationID = nil
        lines = []
        _ = await model.performChatIPC("conversations.delete", payload: [
            "conversation_id": conversationID.uuidString.lowercased(),
        ])
    }
}

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
                    Task { await store.close(model: model); close() }
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
        Task { await store.send(text, model: model) }
    }
}
