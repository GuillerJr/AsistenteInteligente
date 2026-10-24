import SwiftUI

struct MessageListView: View {
    let messages: [ChatMessage]
    let temporary: Bool
    let streamingText: String

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 28) {
                    if messages.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: "sparkle").font(.system(size: 32)).foregroundStyle(.secondary)
                            Text("¿En qué trabajamos?").font(.title2.weight(.semibold))
                            Text(temporary ? "Esta conversación es temporal y no se guarda al cerrar el servicio."
                                 : "Conversa con Codex mediante tu cuenta de ChatGPT.")
                                .foregroundStyle(.secondary).multilineTextAlignment(.center)
                        }.frame(maxWidth: .infinity).padding(.vertical, 80)
                    }
                    ForEach(messages) { message in
                        VStack(alignment: .leading, spacing: 8) {
                            Label(message.role == "user" ? "Tú" : message.role == "assistant" ? "Jarvis" : "Sistema",
                                  systemImage: message.role == "user" ? "person.crop.circle" : "sparkle")
                                .font(.callout.weight(.semibold)).foregroundStyle(.secondary)
                            Text(message.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                        }.id(message.id)
                    }
                    if !streamingText.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("Jarvis · respuesta en curso", systemImage: "sparkle")
                                .font(.callout.weight(.semibold)).foregroundStyle(.secondary)
                            Text(streamingText).textSelection(.enabled)
                        }.id("streaming")
                    }
                }.padding(28).frame(maxWidth: 900).frame(maxWidth: .infinity)
            }
            .onChange(of: messages.last?.id) { _, value in
                if let value { proxy.scrollTo(value, anchor: .bottom) }
            }
            .onChange(of: streamingText) { _, value in
                if !value.isEmpty { proxy.scrollTo("streaming", anchor: .bottom) }
            }
        }
    }
}
