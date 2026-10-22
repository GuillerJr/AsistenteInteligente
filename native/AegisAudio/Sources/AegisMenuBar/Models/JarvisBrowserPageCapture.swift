import AegisAudioCore
import Foundation
import Observation

/// Owns one active page read. A cancelled reader may still return, but can never
/// apply its result or release a newer request's ownership.
@MainActor @Observable
final class JarvisBrowserPageCapture {
    struct Page: Equatable {
        let revision: UUID
        let url: URL
        let title: String
    }

    private var requestID: UUID?
    @ObservationIgnored private var readingTask: Task<Void, Never>?
    var isReading: Bool { requestID != nil }

    func invalidate() {
        requestID = nil
        readingTask?.cancel()
        readingTask = nil
    }

    /// Reserve the page and destination in the button's synchronous action, before
    /// creating a Task that might not start until after navigation or a chat switch.
    @discardableResult
    func start(
        page: Page,
        to chat: JarvisChatStore,
        isCurrentPage: @escaping @MainActor (Page) -> Bool,
        readText: @escaping @MainActor () async throws -> String?
    ) -> Task<Void, Never>? {
        guard !isReading, !Task.isCancelled, !chat.isEditing,
              chat.attachments.count < 4, isCurrentPage(page) else { return nil }
        let request = UUID()
        let conversation = chat.conversationID
        let transcriptRevision = chat.transcriptRevision
        requestID = request
        let task = Task { @MainActor in
            defer {
                if requestID == request { requestID = nil; readingTask = nil }
            }
            @MainActor func ownsResult() -> Bool {
                requestID == request && !Task.isCancelled
                    && chat.conversationID == conversation
                    && chat.transcriptRevision == transcriptRevision
                    && !chat.isEditing && isCurrentPage(page)
            }
            guard ownsResult() else { return }
            do {
                let text = try await readText()
                guard ownsResult() else { return }
                guard let text, !text.isEmpty else {
                    chat.error = String(localized: "Esta página no tiene texto que Jarvis pueda leer.", bundle: .module)
                    return
                }
                let name = String((page.title.isEmpty
                    ? page.url.host() ?? String(localized: "página", bundle: .module)
                    : page.title).prefix(80))
                let document = ChatDocumentAttachment(name: "\(name).txt", content:
                    String(localized: "Página: \(page.url.absoluteString)\n\n\(text)", bundle: .module))
                // Preserve edits to this chat's attachments made while the reader awaited.
                let combined = chat.attachments + [ChatAttachment(document: document)]
                try ChatAttachmentLoader.validate(combined)
                chat.attachments = combined
            } catch {
                guard ownsResult() else { return }
                chat.error = error.localizedDescription
            }
        }
        readingTask = task
        return task
    }
}
