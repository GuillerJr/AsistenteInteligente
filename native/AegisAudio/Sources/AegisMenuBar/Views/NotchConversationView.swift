import AegisAudioCore
import SwiftUI

struct NotchConversationView: View {
    let model: MenuBarModel
    let keepOpen: () -> Void
    let activateVoice: () -> Void
    let reviewInteraction: () -> Void
    @State private var draft = ""

    var body: some View {
        let content = model.mascotPresentation
        VStack(alignment: .leading, spacing: 8) {
            NotchResponseBubble(content: content, understood: model.mascotUnderstoodText)
                .equatable().frame(maxHeight: .infinity)
            if model.mascotSessionAvailable {
                if content.requiresReview {
                    Button("Revisar y responder", action: reviewInteraction)
                        .buttonStyle(NotchConversationButtonStyle())
                        .frame(maxWidth: .infinity, alignment: .trailing)
                        .accessibilityHint("Abre el diálogo de Jarvis. No aprueba ninguna acción.")
                } else {
                    NotchAttachmentStrip(model: model)
                    NotchComposer(model: model, draft: $draft, keepOpen: keepOpen)
                    NotchConversationActions(model: model, activateVoice: activateVoice)
                }
            }
        }
        .onChange(of: model.chat.conversationID) { _, _ in draft = "" }
        .onChange(of: model.mascotSessionAvailable) { _, available in if !available { draft = "" } }
    }
}

/// Resizing the panel must not reparse unchanged Markdown.
private struct NotchResponseBubble: View, Equatable {
    let content: MascotInteractionPresentation
    let understood: String?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                Text(content.title).font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(.cyan)
                if let understood, !content.requiresReview {
                    Text("Entendí: \(understood)").font(.system(size: 11))
                        .foregroundStyle(.secondary).lineLimit(2)
                }
                JarvisChatResponseView(content.message)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(10)
        }.background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct NotchConversationActions: View {
    let model: MenuBarModel
    let activateVoice: () -> Void
    var body: some View {
        HStack(spacing: 8) {
            Text(model.chat.conversationID == nil ? "Nueva conversación" : "Chat activo")
                .font(.system(size: 10)).foregroundStyle(.secondary)
            Spacer()
            if model.voiceState == .listening || model.voiceState == .followingUp {
                Button(action: model.cancelMascotListening) {
                    Label("Cancelar escucha", systemImage: "stop.fill")
                }.disabled(model.mascotCancellingCapture)
            } else if model.chat.isBusy {
                Button { Task { await model.cancelChatResponse() } } label: {
                    Label("Detener", systemImage: "stop.fill")
                }.disabled(model.chat.cancelling)
            } else if model.voiceState == .speaking || model.voiceState == .processing {
                Button(action: activateVoice) {
                    Label("Interrumpir", systemImage: "waveform")
                }.disabled(!model.canActivateMascotVoice)
            } else {
                Text("Doble clic para hablar").font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }.buttonStyle(NotchConversationButtonStyle())
    }
}
