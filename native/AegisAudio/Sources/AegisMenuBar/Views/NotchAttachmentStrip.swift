import SwiftUI

struct NotchAttachmentStrip: View {
    let model: MenuBarModel
    var body: some View {
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
}
