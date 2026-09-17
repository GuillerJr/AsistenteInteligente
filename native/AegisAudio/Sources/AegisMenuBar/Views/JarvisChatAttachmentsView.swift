import AegisAudioCore
import SwiftUI

struct JarvisChatAttachmentsView: View {
    let chat: JarvisChatStore

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(chat.attachments) { attachment in
                    HStack(spacing: 6) {
                        Image(systemName: attachment.isImage ? "photo" : "doc.text")
                        Text(verbatim: attachment.name).lineLimit(1).truncationMode(.middle)
                            .frame(maxWidth: 170)
                        Button { chat.attachments.removeAll { $0.id == attachment.id } } label: {
                            Image(systemName: "xmark").frame(width: 24, height: 24)
                        }.buttonStyle(.plain).disabled(chat.isEditing)
                            .accessibilityLabel("Quitar adjunto \(attachment.name)")
                    }
                    .font(.system(size: 11)).padding(.leading, 10).padding(.trailing, 4).padding(.vertical, 4)
                    .jarvisCard()
                    .help(attachment.name)
                }
            }
        }.fixedSize(horizontal: false, vertical: true)
    }
}
