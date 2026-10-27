import Foundation

public struct SessionContextCheckpoint: Equatable, Sendable {
    public let id: String
    public let sourceRevision: Int
    public let sourceDigest: String
    public let throughSequence: Int
    public let keptMessages: Int
    public let omittedMessages: Int
    public let byteLength: Int
    public let decisions: Int
    public let receipts: Int

    public init(_ value: RPCValue) throws {
        guard let id = value["checkpoint_id"].string, ManagementValidation.identifier(id),
              let source = value["source_revision"].integer, source > 0,
              let digest = value["source_digest"].string, ManagementValidation.hash(digest),
              let through = value["through_sequence"].integer, through > 0, through <= source,
              let kept = value["kept_message_count"].integer, kept >= 0,
              let omitted = value["omitted_message_count"].integer, omitted >= 0,
              let bytes = value["byte_length"].integer, (1...16000).contains(bytes),
              let decisions = value["decisions_count"].integer, (0...16).contains(decisions),
              let receipts = value["receipts_count"].integer, receipts >= 0,
              kept + omitted <= through else { throw RPCFailure.invalidResponse }
        self.id = id; sourceRevision = source; sourceDigest = digest; throughSequence = through
        keptMessages = kept; omittedMessages = omitted; byteLength = bytes
        self.decisions = decisions; self.receipts = receipts
    }
}

public struct SessionContextSnapshot: Equatable, Sendable {
    public let revision: Int
    public let messageCount: Int
    public let checkpoint: SessionContextCheckpoint?

    public init(_ value: RPCValue) throws {
        guard let revision = value["revision"].integer, revision >= 0,
              let count = value["message_count"].integer, count >= 0, count <= revision else {
            throw RPCFailure.invalidResponse
        }
        self.revision = revision; messageCount = count
        checkpoint = value["checkpoint"] == .null ? nil : try SessionContextCheckpoint(value["checkpoint"])
        if let checkpoint, checkpoint.sourceRevision >= revision { throw RPCFailure.invalidResponse }
    }
}

public struct SessionBranchSnapshot: Equatable, Sendable {
    public let scope: SessionScope
    public let title: String
    public let agentID: String
    public let parentID: String
    public let sourceRevision: Int
    public let throughSequence: Int
    public let sourceDigest: String

    public init(session: RPCValue, lineage: RPCValue, source: SessionScope) throws {
        guard let id = session["session_id"].string, ManagementValidation.identifier(id), id != source.sessionID,
              let temporary = session["temporary"].bool, temporary == source.temporary,
              let title = session["title"].string, ManagementValidation.text(title, limit: 512),
              let agent = session["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              session["parent_session_id"].string == source.sessionID,
              let kind = lineage["kind"].string, ["fork", "rewind"].contains(kind), lineage["source_session_id"].string == source.sessionID,
              let revision = lineage["source_revision"].integer, revision >= 0,
              let through = lineage["until_sequence"].integer, through >= 0, through <= revision,
              kind == "fork" ? through == revision : (revision > 0 && through > 0),
              let digest = lineage["source_digest"].string, ManagementValidation.hash(digest),
              lineage["effects_reverted"].bool == false,
              lineage["permissions_inherited"].bool == false,
              lineage["provider_thread_inherited"].bool == false else { throw RPCFailure.invalidResponse }
        scope = SessionScope(sessionID: id, temporary: temporary); self.title = title; agentID = agent
        parentID = source.sessionID; sourceRevision = revision; throughSequence = through; sourceDigest = digest
    }
}

/// Only typed service-authored system records get a compact presentation. A
/// model/user string with the same prefix never becomes an action receipt.
public enum SessionContextPresentation {
    public static func systemRecord(_ text: String, role: String) -> String? {
        guard role == "system", text.utf8.count <= 512000 else { return nil }
        let compact = "JARVIS_SESSION_CONTEXT_V1\n", lineage = "JARVIS_SESSION_LINEAGE_V1\n"
        let operation = "JARVIS_SESSION_OPERATION_V1\n"
        let prefix = [compact, lineage, operation].first(where: { text.hasPrefix($0) })
        guard let prefix, let data = text.dropFirst(prefix.count).data(using: .utf8),
              let value = try? JSONDecoder().decode(RPCValue.self, from: data) else { return nil }
        if prefix == compact, value["kind"].string == "jarvis.session.compaction.v1",
           let checkpoint = try? SessionContextCheckpoint(value["checkpoint"]) {
            return "Contexto compactado: \(checkpoint.keptMessages) mensajes conservados y \(checkpoint.omittedMessages) omitidos del contexto del modelo. El historial original sigue disponible."
        }
        if prefix == lineage, let kind = value["kind"].string, ["fork", "rewind"].contains(kind),
           let sequence = value["until_sequence"].integer, sequence >= 0,
           value["effects_reverted"].bool == false, value["permissions_inherited"].bool == false {
            return "Rama desde el punto \(sequence). Los efectos anteriores se conservan y requieren sus propias comprobaciones."
        }
        if prefix == operation, value["recorded"].bool == true,
           value["historical_ack_established"].bool == false, value["permits_replay"].bool == false,
           let id = value["operation_id"].string, ManagementValidation.identifier(id),
           let kind = value["operation_kind"].string, ["compact", "rewind", "fork"].contains(kind) {
            return kind == "compact" ? "Registro local de compactación. El historial original se conserva."
                : "Registro local de creación de rama. Las acciones anteriores no se repiten."
        }
        return nil
    }
}
