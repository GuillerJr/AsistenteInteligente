import AegisAudioCore
import Foundation
import Observation

struct ProjectConversationPage {
    let conversations: [ChatConversationSummary]
    let nextCursor: String?

    init(_ data: Data, projectID: String) throws {
        guard let expectedID = UUID(uuidString: projectID),
              let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rawProject = payload["project_id"] as? String,
              UUID(uuidString: rawProject) == expectedID,
              payload.keys.contains("next_cursor") else { throw LocalIPCError.malformedResponse }
        let rows = try ChatConversationSummary.page(data).0
        guard rows.allSatisfy({ $0.projectID == expectedID && !$0.archived }),
              Set(rows.map(\.id)).count == rows.count else { throw LocalIPCError.malformedResponse }
        if payload["next_cursor"] is NSNull {
            nextCursor = nil
        } else {
            guard let cursor = payload["next_cursor"] as? String,
                  Self.validCursor(cursor), !rows.isEmpty else { throw LocalIPCError.malformedResponse }
            nextCursor = cursor
        }
        conversations = rows
    }

    private static func validCursor(_ value: String) -> Bool {
        guard value.utf8.count <= 1_024 else { return false }
        let parts = value.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 2, (1...900).contains(parts[0].utf8.count),
              parts[1].utf8.count == 64 else { return false }
        return parts[0].utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0)
                || $0 == 45 || $0 == 95
        } && parts[1].utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}

@MainActor @Observable
final class ProjectConversationStore {
    private(set) var projectID: String?
    private(set) var conversations: [ChatConversationSummary] = []
    private(set) var nextCursor: String?
    private(set) var loading = false
    private(set) var error: String?
    private var generation = UUID()

    func load(
        projectID: String, append: Bool = false,
        fetch: @MainActor ([String: Any]) async -> Data?
    ) async {
        let cursor: String?
        if append {
            guard !loading, self.projectID == projectID, let nextCursor else { return }
            cursor = nextCursor
        } else {
            generation = UUID()
            self.projectID = projectID
            conversations = []
            nextCursor = nil
            cursor = nil
        }
        let requestGeneration = generation
        loading = true
        error = nil
        defer { if generation == requestGeneration { loading = false } }
        var payload: [String: Any] = ["project_id": projectID, "limit": 30]
        if let cursor { payload["cursor"] = cursor }
        let data = await fetch(payload)
        guard generation == requestGeneration, self.projectID == projectID,
              !Task.isCancelled else { return }
        do {
            guard let data else { throw LocalIPCError.malformedResponse }
            let page = try ProjectConversationPage(data, projectID: projectID)
            guard page.nextCursor == nil || page.nextCursor != cursor else {
                throw LocalIPCError.malformedResponse
            }
            let seen = Set(conversations.map(\.id))
            conversations += page.conversations.filter { !seen.contains($0.id) }
            nextCursor = page.nextCursor
        } catch {
            self.error = String(localized: "No pude leer el historial. Inténtalo de nuevo.", bundle: .module)
        }
    }
}
