import AegisAudioCore
import Foundation

/// One turn's mode and attachments, captured before asynchronous submission.
struct ChatSubmission: Sendable {
    let text: String
    let attachments: [ChatAttachment]
    let planMode: Bool

    var method: String { attachments.contains(where: \.isImage) ? "image.submit" : "swarm.submit" }

    func payload(conversationID: UUID) -> [String: Any] {
        var payload: [String: Any] = [
            "text": text, "conversation_id": conversationID.uuidString.lowercased(),
        ]
        let documents = attachments.compactMap(\.document)
        if !documents.isEmpty {
            payload["documents"] = documents.map { ["name": $0.name, "content": $0.content] }
        }
        if let attachment = attachments.first(where: \.isImage), let image = attachment.image {
            payload["image"] = ["media_type": image.mediaType, "data_base64": image.data.base64EncodedString()]
            payload["image_name"] = attachment.name
        }
        if planMode { payload["plan_mode"] = true }
        return payload
    }
}
