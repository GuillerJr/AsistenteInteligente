import AegisAudioCore
import Foundation

/// Recovery is limited to failures before a verified result. Uncertain tool outcomes
/// deliberately offer no automatic replay, which could duplicate an external action.
enum ChatRecovery {
    static func canRetry(_ code: String) -> Bool {
        ["remote_provider_unavailable", "remote_rate_limited", "remote_authentication_failed",
         "brain_unavailable"].contains(code)
    }

    static func message(_ code: String) -> String {
        switch code {
        case "remote_authentication_failed":
            String(localized: "NVIDIA rechazó la clave. Actualízala en Configuración › Cuentas y pulsa Reintentar.", bundle: .module)
        case "remote_provider_unavailable":
            String(localized: "No pude conectar con NVIDIA. Comprueba tu conexión a Internet y vuelve a intentarlo.", bundle: .module)
        case "daemon_unavailable", "job_status_unavailable":
            String(localized: "Se perdió la conexión con Jarvis. Comprueba el estado de la tarea antes de volver a enviarla.", bundle: .module)
        default:
            LocalVoiceTurnFeedback.spokenJobFailure(for: code)
                ?? String(localized: "No pude completar la solicitud. Inténtalo de nuevo.", bundle: .module)
        }
    }
}

struct ChatReplayDraft: Sendable {
    let conversation: ChatConversationSummary
    let text: String
    let attachments: [ChatAttachment]
    let missingImage: String?

    init(_ data: Data) throws {
        conversation = try ChatConversationSummary.record(data)
        guard let row = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let replay = row["replay"] as? [String: Any],
              let text = replay["text"] as? String, !text.isEmpty, text.utf8.count <= 16_384,
              let documents = replay["documents"] as? [[String: Any]], documents.count <= 4 else {
            throw LocalIPCError.malformedResponse
        }
        self.text = text
        attachments = try documents.map {
            guard let name = $0["name"] as? String, let content = $0["content"] as? String else {
                throw LocalIPCError.malformedResponse
            }
            return ChatAttachment(document: .init(name: name, content: content))
        }
        try ChatAttachmentLoader.validate(attachments)
        missingImage = replay["image_name"] as? String
    }
}
