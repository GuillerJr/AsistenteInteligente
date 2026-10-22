import AegisAudioCore
import Foundation

/// Session-only composer state. This type deliberately has no serialization or disk storage.
struct ChatComposerDraft: Equatable {
    let text: String?
    let attachments: [ChatAttachment]

    var isEmpty: Bool { (text?.isEmpty ?? true) && attachments.isEmpty }
    var byteCount: Int {
        (text?.utf8.count ?? 0) + attachments.reduce(0) { total, attachment in
            total + attachment.name.utf8.count
                + (attachment.document?.content.utf8.count ?? attachment.image?.data.count ?? 0)
        }
    }
}

/// Keep at most 20 inactive drafts and 4 MiB of payload in RAM. Evict the least
/// recently left conversation first; never truncate a draft or an attachment.
/// An individual draft larger than the budget is not retained when leaving.
struct ChatDraftCache {
    enum Discard { case none, oldest, oversized }
    private let maximumCount: Int
    private let maximumBytes: Int
    private var drafts: [UUID: ChatComposerDraft] = [:]
    private var order: [UUID] = []

    init(maximumCount: Int = 20, maximumBytes: Int = 4 * 1_024 * 1_024) {
        self.maximumCount = max(1, maximumCount)
        self.maximumBytes = max(1, maximumBytes)
    }

    mutating func remove(_ id: UUID) {
        drafts.removeValue(forKey: id)
        order.removeAll { $0 == id }
    }

    mutating func take(_ id: UUID) -> ChatComposerDraft? {
        let draft = drafts[id]
        remove(id)
        return draft
    }

    mutating func store(_ draft: ChatComposerDraft, for id: UUID) -> Discard {
        remove(id)
        guard !draft.isEmpty else { return .none }
        guard draft.byteCount <= maximumBytes else { return .oversized }
        drafts[id] = draft
        order.append(id)
        var discarded = false
        while order.count > maximumCount
            || drafts.values.reduce(0, { $0 + $1.byteCount }) > maximumBytes {
            guard let oldest = order.first else { break }
            remove(oldest)
            discarded = true
        }
        return discarded ? .oldest : .none
    }
}

extension JarvisChatStore {
    func switchConversation(to id: UUID) {
        let sameConversation = conversationID == id
        let incomingDraft = sameConversation
            ? ChatComposerDraft(text: composerDraft, attachments: attachments)
            : draftCache.take(id)
        resetConversation(preservingDraft: !sameConversation)
        conversationID = id
        composerDraft = incomingDraft?.text
        attachments = incomingDraft?.attachments ?? []
    }

    func adoptVoiceConversation(_ id: UUID, requestedConversationID: UUID?) {
        guard conversationID != id else { return }
        // A nil requested ID creates this chat. A supplied ID resumes an existing
        // conversation, even when the text UI has not selected one since startup.
        if conversationID == nil, requestedConversationID == nil {
            conversationID = id
            return
        }
        switchConversation(to: id)
    }

    func retainCurrentDraft() -> ChatDraftCache.Discard {
        // A fresh chat has no history entry to return to. Temporary chats are
        // deleted on leave, so neither kind contributes to the retained cache.
        guard let id = conversationID else { return .none }
        guard !temporary else { draftCache.remove(id); return .none }
        return draftCache.store(.init(text: composerDraft, attachments: attachments), for: id)
    }

    func discardDraft(for id: UUID) {
        draftCache.remove(id)
        if conversationID == id {
            composerDraft = nil
            attachments = []
        }
    }

    func restoreRejectedComposer(_ text: String, conversationID expectedID: UUID?, revision: UUID) {
        guard conversationID == expectedID, transcriptRevision == revision,
              composerDraft?.isEmpty != false else { return }
        composerDraft = text
    }

    @discardableResult
    func beginSubmission(_ submission: ChatSubmission, consumingComposerText: String?) -> Bool {
        guard transcript.begin(submission.text, attachmentNames: submission.attachments.map(\.name))
        else { return false }
        // Queue/replay/plan submissions must not consume a new composer draft,
        // even when its text happens to match the automatic request exactly.
        if let consumingComposerText { consumeSubmittedDraft(consumingComposerText) }
        return true
    }

    /// A submitted composer message must not return as a draft. Keep any newer
    /// text entered between the send gesture and the beginning of submission.
    func consumeSubmittedDraft(_ text: String) {
        if composerDraft?.trimmingCharacters(in: .whitespacesAndNewlines)
            == text.trimmingCharacters(in: .whitespacesAndNewlines) {
            composerDraft = nil
        }
        if let id = conversationID { draftCache.remove(id) }
    }

    func consumeSubmittedAttachments(_ submitted: [ChatAttachment]) {
        let ids = Set(submitted.map(\.id))
        attachments.removeAll { ids.contains($0.id) }
        if let id = conversationID { draftCache.remove(id) }
    }
}
