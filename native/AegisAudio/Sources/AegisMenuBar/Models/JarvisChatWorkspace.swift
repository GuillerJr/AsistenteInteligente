import Foundation

enum JarvisWorkspacePanel: Equatable {
    case browser, sideChat, context
}

enum JarvisChatStarter: String, CaseIterable, Identifiable {
    case organize, write, plan, summarize
    var id: String { rawValue }
    var title: String {
        switch self {
        case .organize: String(localized: "Organizar tareas", bundle: .module)
        case .write: String(localized: "Redactar un texto", bundle: .module)
        case .plan: String(localized: "Preparar un plan", bundle: .module)
        case .summarize: String(localized: "Resumir un documento", bundle: .module)
        }
    }
    var symbol: String {
        switch self {
        case .organize: "checklist"
        case .write: "square.and.pencil"
        case .plan: "list.bullet.clipboard"
        case .summarize: "doc.text"
        }
    }
    var prompt: String {
        switch self {
        case .organize: String(localized: "Ayúdame a organizar mis tareas por prioridad. Estas son las tareas: ", bundle: .module)
        case .write: String(localized: "Ayúdame a redactar un texto claro. El tema y el destinatario son: ", bundle: .module)
        case .plan: String(localized: "Prepara un plan paso a paso para este objetivo: ", bundle: .module)
        case .summarize: String(localized: "Resume el documento que adjunte y destaca sus ideas principales y los siguientes pasos.", bundle: .module)
        }
    }
}

extension JarvisChatStore {
    var workspaceProject: JarvisProject? {
        guard !temporary else { return nil }
        if let conversation = currentConversation {
            return project(conversation.projectID?.uuidString.lowercased())
        }
        guard conversationID == nil else { return nil }
        return project(newChatProjectID)
    }

    var canChooseWorkspaceProject: Bool {
        !temporary && !isEditing && conversationID == nil && transcript.messages.isEmpty
    }

    /// Context selection for an unsent chat preserves its draft and attachments.
    @discardableResult
    func chooseWorkspaceProject(_ id: String?) -> Bool {
        guard canChooseWorkspaceProject, id == nil || project(id) != nil else { return false }
        newChatProjectID = id
        return true
    }

    /// Suggestions only prepare a draft; the owner remains responsible for sending it.
    @discardableResult
    func prepareStarter(_ starter: JarvisChatStarter) -> Bool {
        guard !isEditing, transcript.messages.isEmpty,
              (composerDraft ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return false
        }
        composerDraft = starter.prompt
        if starter == .plan { planMode = true }
        return true
    }
}
