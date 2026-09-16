import Foundation

public struct IPCActionPreview: Equatable, Sendable {
    public struct Field: Equatable, Sendable {
        public let label: String
        public let value: String
    }
    public let fields: [Field]

    init?(object: Any) {
        guard let object = object as? [String: Any], Set(object.keys) == ["fields"],
              let rawFields = object["fields"] as? [[String: Any]], rawFields.count <= 32,
              let data = try? JSONSerialization.data(withJSONObject: object), data.count <= 24_000
        else { return nil }
        var fields: [Field] = []
        for field in rawFields {
            guard Set(field.keys) == ["label", "value"],
                  let label = field["label"] as? String, !label.isEmpty, label.count <= 128,
                  let value = field["value"] as? String, !value.isEmpty, value.count <= 16_000
            else { return nil }
            fields.append(Field(label: label, value: value))
        }
        self.fields = fields
    }
}

/// Labels come from known tool operations, never from generated prose.
public enum ActionApprovalLabel {
    public static func title(toolName: String, action: String? = nil) -> String {
        let operation = toolName.components(separatedBy: "__").last ?? toolName
        return switch operation {
        case "mail_send_message": "Enviar correo"
        case "draft_gmail_message": "Guardar borrador"
        case "calendar_create_event": "Crear evento"
        case "manage_event": action == "create" ? "Crear evento"
            : (action == "update" ? "Actualizar evento" : "Aprobar una vez")
        case "create_drive_file": "Crear archivo"
        case "update_drive_file": "Actualizar archivo"
        case "create_drive_folder": "Crear carpeta"
        case "reminder_create": "Crear recordatorio"
        case "reminder_complete": "Completar recordatorio"
        case "contact_create": "Crear contacto"
        case "computer_use": "Autorizar control"
        case "shortcut_run": "Ejecutar atajo"
        case "browser_open_url": "Abrir dirección"
        case "application_open": "Abrir aplicación"
        case "browser_search": "Buscar en Internet"
        default: "Aprobar una vez"
        }
    }
}

public struct IPCInputRequest: Equatable, Sendable {
    public let question: String
    public let requestText: String
    public let expiresAt: Date

    init?(object: Any) {
        guard let object = object as? [String: Any],
              Set(object.keys) == ["question", "request_text", "expires_at"],
              let question = object["question"] as? String, !question.isEmpty, question.count <= 1_000,
              let text = object["request_text"] as? String, !text.isEmpty, text.count <= 4_096,
              let expiry = object["expires_at"] as? String, expiry.utf8.count <= 64,
              let date = IPCPendingConfirmation.parseDate(expiry)
        else { return nil }
        self.question = question
        requestText = text
        expiresAt = date
    }
}

#if DEBUG
public extension IPCInputRequest {
    static func debugPreview(question: String, requestText: String) -> IPCInputRequest? {
        let formatter = ISO8601DateFormatter()
        return IPCInputRequest(object: ["question": question, "request_text": requestText,
                                        "expires_at": formatter.string(from: Date().addingTimeInterval(300))])
    }
}
#endif
