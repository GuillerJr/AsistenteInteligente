import Foundation

enum JarvisComposerAvailability {
    enum Submission: Equatable { case send, queue }

    static func validDraft(_ text: String, hasAttachments: Bool) -> Bool {
        (hasAttachments || !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            && text.utf8.count <= 4_096
    }

    static func submission(_ text: String, hasAttachments: Bool, running: Bool,
                           cancelling: Bool, canSend: Bool, canQueue: Bool,
                           interactive: Bool) -> Submission? {
        guard interactive, validDraft(text, hasAttachments: hasAttachments) else { return nil }
        if running { return canQueue && !hasAttachments && !cancelling ? .queue : nil }
        return canSend ? .send : nil
    }

    static func placeholder(running: Bool, planning: Bool, inProject: Bool) -> String {
        if running { return String(localized: "Añade una indicación para después…", bundle: .module) }
        if planning { return String(localized: "¿Qué quieres planificar?", bundle: .module) }
        if inProject { return String(localized: "Describe lo que necesitas para este proyecto…", bundle: .module) }
        return String(localized: "Pregúntale a Jarvis…", bundle: .module)
    }
}
