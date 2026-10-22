import AegisAudioCore
import CryptoKit
import Foundation

extension JarvisChatStore {
    struct HistoryMatchNavigation: Equatable {
        let id = UUID()
        let conversationID: UUID
        let turnID: UUID
        let sequence: Int
        let transcriptRevision: UUID
        let searchGeneration: UUID
    }

    struct HistoryCriteria: Equatable {
        let query: String
        let archived: Bool
        let filed: Bool
    }

    var historyCriteria: HistoryCriteria {
        HistoryCriteria(query: historyQuery.trimmingCharacters(in: .whitespacesAndNewlines),
                        archived: showTrash, filed: showArchived)
    }

    var historySearchIncomplete: Bool { nextSearchCursor != nil || historySearchPaused }

    /// Invalidate at the edit itself, before the view's debounce starts a new task.
    func invalidateHistorySearch() {
        historyGeneration = UUID()
        historyLoading = false
        historyError = nil
        historySearchPaused = false
        historyMatchNavigation = nil
        conversations = []
        nextOffset = nil
        nextSearchCursor = nil
        historySearchCursorDigests = []
    }

    func pauseHistorySearch() {
        guard historyLoading, !historyCriteria.query.isEmpty else { return }
        historyGeneration = UUID()
        historyLoading = false
        historySearchPaused = true
    }

    /// The view supplies a layout boundary. Revalidate both sides of that await,
    /// so an old result can never scroll a newly selected conversation.
    func navigateToHistoryMatch(
        _ request: HistoryMatchNavigation,
        afterLayout: @MainActor () async -> Void,
        scroll: @MainActor (UUID) -> Void
    ) async {
        guard isCurrentHistoryMatch(request), !Task.isCancelled else { return }
        await afterLayout()
        guard isCurrentHistoryMatch(request), !Task.isCancelled else { return }
        scroll(request.turnID)
        historyMatchNavigation = nil
    }

    private func isCurrentHistoryMatch(_ request: HistoryMatchNavigation) -> Bool {
        historyMatchNavigation == request && conversationID == request.conversationID
            && pane == .chat && transcriptRevision == request.transcriptRevision
            && historyGeneration == request.searchGeneration
            && transcript.hasNewerHistory
            && transcript.messages.contains { $0.id == request.turnID && $0.sequence == request.sequence }
    }

    /// A bounded series of short IPC calls preserves progress without freezing the sidebar.
    /// Empty pages with a cursor are incomplete searches, not "no matches".
    func refreshHistory(
        append: Bool = false,
        fetch: @MainActor (String, [String: Any]) async -> Data?
    ) async {
        guard !Task.isCancelled else { return }
        guard !append || !historyLoading else { return }
        let criteria = historyCriteria
        let searching = !criteria.query.isEmpty
        if append {
            guard searching ? historySearchIncomplete : nextOffset != nil else { return }
        }
        let generation = UUID()
        historyGeneration = generation
        historyLoading = true
        historyError = nil
        historySearchPaused = false
        if !append {
            nextOffset = nil
            nextSearchCursor = nil
            historySearchCursorDigests = []
        }
        defer { if historyGeneration == generation { historyLoading = false } }
        let initialCount = append ? conversations.count : 0
        var replacing = !append
        for _ in 0..<(searching ? 32 : 1) {
            guard generation == historyGeneration, criteria == historyCriteria,
                  !Task.isCancelled else { return }
            var payload: [String: Any] = ["archived": criteria.archived, "filed": criteria.filed,
                                          "query": criteria.query, "limit": 30]
            if searching {
                payload["limit"] = max(1, 30 - (replacing ? 0 : conversations.count - initialCount))
                if let nextSearchCursor { payload["cursor"] = nextSearchCursor }
            } else { payload["offset"] = append ? nextOffset ?? 0 : 0 }
            let data = await fetch(searching ? "conversations.search" : "conversations.list", payload)
            guard generation == historyGeneration, criteria == historyCriteria,
                  !Task.isCancelled else { return }
            do {
                guard let data else { throw LocalIPCError.malformedResponse }
                let entries: [ChatConversationSummary]
                if searching {
                    let page = try ChatSearchPage(data, archived: criteria.archived, filed: criteria.filed)
                    if let cursor = page.nextCursor {
                        let digest = SHA256.hash(data: Data(cursor.utf8)).description
                        guard !historySearchCursorDigests.contains(digest) else {
                            throw LocalIPCError.malformedResponse
                        }
                        // Each batch is bounded to 32 calls. Retain a bounded window
                        // of prior cursors as protection against a faulty server loop.
                        if historySearchCursorDigests.count >= 1_024 {
                            historySearchCursorDigests.removeAll(keepingCapacity: true)
                        }
                        historySearchCursorDigests.insert(digest)
                    }
                    entries = page.conversations
                    nextSearchCursor = page.nextCursor
                    nextOffset = nil
                } else {
                    (entries, nextOffset) = try ChatConversationSummary.page(data)
                }
                for entry in entries where entry.archived { draftCache.remove(entry.id) }
                let existing = replacing ? [] : conversations
                let ids = Set(existing.map(\.id))
                conversations = existing + entries.filter { !ids.contains($0.id) }
                replacing = false
                if let current = conversations.first(where: { $0.id == conversationID && !$0.archived }) {
                    selectedConversation = current
                }
                if !searching || nextSearchCursor == nil || conversations.count - initialCount >= 30 {
                    return
                }
            } catch {
                historyError = String(localized: "No pude leer el historial. Inténtalo de nuevo.", bundle: .module)
                return
            }
        }
        // Preserve the authenticated cursor so the owner can keep scanning a large history.
    }
}
