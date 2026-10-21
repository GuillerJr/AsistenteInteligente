import Foundation
import Observation

/// A temporary conversation beside the main chat. Every suspension can outlive
/// its panel: a generation owns the reply and cleans up any late IPC resources.
@MainActor @Observable
final class SideChatStore {
    typealias IPC = @MainActor (String, [String: Any]) async -> Data?

    struct Line: Identifiable, Equatable {
        let id = UUID()
        let fromOwner: Bool
        var text: String
    }

    var lines: [Line] = []
    var busy = false
    private var generation = UUID()
    private var conversationID: String?
    private var jobID: String?

    func send(_ text: String, using ipc: IPC) async {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, question.utf8.count <= 4_096, !busy else { return }
        let turn = generation
        let reply = Line(fromOwner: false, text: "")
        busy = true
        defer {
            if generation == turn { busy = false; jobID = nil }
        }
        lines.append(Line(fromOwner: true, text: question))
        lines.append(reply)

        if conversationID == nil {
            let created = Self.object(await ipc("conversations.create", [
                "title": "Chat lateral", "temporary": true,
            ]))
            let raw = created?["conversation_id"] as? String
            let createdID = raw.flatMap(UUID.init(uuidString:))?.uuidString.lowercased()
            guard generation == turn else {
                if let createdID { await Self.delete(createdID, using: ipc) }
                return
            }
            conversationID = createdID
        }
        guard let conversation = conversationID else {
            update(reply.id, turn: turn, text: "No pude enviar la pregunta. Inténtalo de nuevo.")
            return
        }
        let submitted = Self.object(await ipc("swarm.submit", [
            "text": question, "conversation_id": conversation,
        ]))
        let submittedID = submitted?["job_id"] as? String
        guard generation == turn else {
            if let submittedID { await Self.cancel(submittedID, using: ipc) }
            await Self.delete(conversation, using: ipc)
            return
        }
        guard var snapshot = submitted, let submittedID else {
            update(reply.id, turn: turn, text: "No pude enviar la pregunta. Inténtalo de nuevo.")
            return
        }
        jobID = submittedID
        for _ in 0 ..< 60 {
            guard generation == turn else { return }
            let status = snapshot["status"] as? String ?? ""
            if let partial = snapshot["partial_result"] as? String, !partial.isEmpty {
                update(reply.id, turn: turn, text: partial)
            }
            if ["completed", "failed", "cancelled"].contains(status) {
                if let result = snapshot["result"] as? String, !result.isEmpty {
                    update(reply.id, turn: turn, text: result)
                } else if status != "completed" {
                    update(reply.id, turn: turn, text:
                        "No pude completar la pregunta (\(snapshot["error_code"] as? String ?? status)).")
                }
                return
            }
            if status == "awaiting_confirmation" {
                await Self.cancel(submittedID, using: ipc)
                update(reply.id, turn: turn, text: "Eso necesita tu aprobación: pídelo en el chat principal.")
                return
            }
            let next = Self.object(await ipc("jobs.wait", [
                "job_id": submittedID,
                "after_stream_version": snapshot["stream_version"] as? Int ?? 0,
                "timeout_milliseconds": 10_000,
            ]))
            guard generation == turn else { return }
            guard let next else {
                await Self.cancel(submittedID, using: ipc)
                update(reply.id, turn: turn, text: "Perdí la conexión con Jarvis.")
                return
            }
            snapshot = next
        }
        await Self.cancel(submittedID, using: ipc)
        update(reply.id, turn: turn, text: "La respuesta tardó demasiado.")
    }

    func close(using ipc: IPC) async {
        let oldJob = jobID
        let oldConversation = conversationID
        generation = UUID()
        jobID = nil
        conversationID = nil
        lines = []
        busy = false
        if let oldJob { await Self.cancel(oldJob, using: ipc) }
        if let oldConversation { await Self.delete(oldConversation, using: ipc) }
    }

    private func update(_ reply: UUID, turn: UUID, text: String) {
        guard generation == turn, let index = lines.firstIndex(where: { $0.id == reply }) else { return }
        lines[index].text = text
    }

    private static func object(_ data: Data?) -> [String: Any]? {
        guard let data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private static func cancel(_ jobID: String, using ipc: IPC) async {
        _ = await ipc("jobs.cancel", ["job_id": jobID])
    }

    private static func delete(_ conversationID: String, using ipc: IPC) async {
        _ = await ipc("conversations.delete", ["conversation_id": conversationID])
    }
}
