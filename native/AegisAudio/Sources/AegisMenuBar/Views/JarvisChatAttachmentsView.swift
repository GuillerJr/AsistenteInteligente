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
                            Image(systemName: "xmark")
                        }.buttonStyle(.plain).disabled(chat.isEditing)
                            .accessibilityLabel("Quitar adjunto \(attachment.name)")
                    }
                    .font(.system(size: 11)).padding(8)
                    .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                    .help(attachment.name)
                }
            }
        }.fixedSize(horizontal: false, vertical: true)
    }
}
