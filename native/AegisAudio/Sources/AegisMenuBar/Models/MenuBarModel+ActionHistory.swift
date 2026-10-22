import AegisAudioCore
import Foundation

extension MenuBarModel {
    func fetchActionHistory(_ payload: [String: Any]) async throws -> Data {
        try await actionHistoryRequest(payload: payload, deletion: nil)
    }

    func deleteActionHistory(revision: UUID, entryID: UUID?) async throws -> Data {
        try await actionHistoryRequest(payload: [:], deletion: (revision, entryID))
    }

    private func actionHistoryRequest(payload: [String: Any], deletion: (UUID, UUID?)?) async throws -> Data {
        guard canReadChatHistory, let permit = userSessionExecutionGate.permit,
              let secret = ipcSecret else { throw JarvisActionHistoryError.unavailable }
        let encoded = try JSONSerialization.data(withJSONObject: payload)
        let result = await Task.detached(priority: .userInitiated) { () -> (Data?, String?) in
            do {
                let client = try LocalIPCClient(secret: secret)
                let response: LocalIPCResponse
                if let (revision, entryID) = deletion {
                    response = try client.deleteActionHistory(revision: revision, entryID: entryID)
                } else {
                    let decoded = try JSONSerialization.jsonObject(with: encoded) as? [String: Any] ?? [:]
                    response = try client.call(method: "actions.history.list", payload: decoded)
                }
                guard response.ok else { return (nil, response.errorCode) }
                return (try JSONSerialization.data(withJSONObject: response.payload), nil)
            } catch { return (nil, "transport_unavailable") }
        }.value
        guard userSessionExecutionGate.isCurrent(permit), !Task.isCancelled else {
            throw JarvisActionHistoryError.unavailable
        }
        if let data = result.0 { return data }
        if result.1 == "action_history_changed" { throw JarvisActionHistoryError.changed }
        if deletion != nil { throw JarvisActionHistoryError.deletionUnconfirmed }
        if result.1 == "action_history_invalid" { throw JarvisActionHistoryError.invalid }
        throw JarvisActionHistoryError.unavailable
    }
}
