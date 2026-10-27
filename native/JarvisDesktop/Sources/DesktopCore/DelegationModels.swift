import Foundation

public struct DelegatedTaskSpec: Equatable, Sendable, Identifiable {
    public let id: UUID
    public var agentID: String
    public var agentRevision: Int
    public var message: String
    public var maxSteps: Int
    public init(id: UUID = UUID(), agentID: String, agentRevision: Int, message: String = "", maxSteps: Int = 10) {
        self.id = id; self.agentID = agentID; self.agentRevision = agentRevision
        self.message = message; self.maxSteps = maxSteps
    }
    public var params: RPCValue {
        .object(["agent_id": .string(agentID), "expected_revision": .number(Double(agentRevision)),
                 "message": .string(message), "max_steps": .number(Double(maxSteps))])
    }
    public var valid: Bool {
        ManagementValidation.identifier(agentID, agent: true) && agentRevision > 0
        && ManagementValidation.text(message, limit: 8000) && (1...32).contains(maxSteps)
    }
}

public struct DelegatedChildSnapshot: Identifiable, Equatable, Sendable {
    public let index: Int
    public let agentID: String
    public let agentRevision: Int
    public let sessionID: String
    public let runID: String
    public let maxSteps: Int
    public let status: String
    public let phase: String
    public let outcome: String
    public let answer: String?
    public let answerTruncated: Bool
    public let answerBytes: Int
    public let answerDigest: String?
    public let receiptsTruncated: Bool
    public let receiptsTotal: Int
    public let unknownEffects: Bool
    public let receipts: [ActionReceipt]
    public let settled: Bool
    public var id: String { runID }
    public init(_ value: RPCValue) throws {
        let terminal = ["completed", "failed", "cancelled", "interrupted"]
        guard let index = value["index"].integer, (0...3).contains(index),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let revision = value["agent_revision"].integer, revision > 0,
              let session = value["session_id"].string, ManagementValidation.identifier(session),
              let run = value["run_id"].string, ManagementValidation.identifier(run),
              let steps = value["max_steps"].integer, (1...32).contains(steps),
              let status = value["status"].string,
              (terminal + ["queued", "running", "waiting_approval", "cancelling", "cleaning"]).contains(status),
              let phase = value["phase"].string, ["queued", "running", "waiting_approval", "cleaning", "settled"].contains(phase),
              let outcome = value["outcome"].string, ["pending", "known", "unknown"].contains(outcome),
              let settled = value["settled"].bool, settled == terminal.contains(status),
              settled == (outcome != "pending"), status != "completed" || outcome == "known"
        else { throw RPCFailure.invalidResponse }
        guard let answerTruncated = value["answer_truncated"].bool,
              let answerBytes = value["answer_bytes"].integer, answerBytes >= 0 else { throw RPCFailure.invalidResponse }
        if value["answer"] != .null {
            guard let answer = value["answer"].string, ManagementValidation.text(answer, limit: 65536, empty: true),
                  value["answer_source"].string == "model", answerBytes >= answer.utf8.count,
                  answerTruncated == (answerBytes > answer.utf8.count) else { throw RPCFailure.invalidResponse }
            if !answer.isEmpty {
                guard let digest = value["answer_sha256"].string, ManagementValidation.hash(digest),
                      answerTruncated || digest == ManagementValidation.digest(answer) else { throw RPCFailure.invalidResponse }
            } else if value["answer_sha256"] != .null { throw RPCFailure.invalidResponse }
        } else if value["answer_source"] != .null || answerBytes != 0 || answerTruncated || value["answer_sha256"] != .null { throw RPCFailure.invalidResponse }
        receipts = try ManagementValidation.rows(value["action_receipts"], limit: 256, parse: ActionReceipt.init)
        guard Set(receipts.map(\.id)).count == receipts.count,
              let receiptTotal = value["action_receipts_total"].integer, receiptTotal >= receipts.count,
              let truncated = value["action_receipts_truncated"].bool,
              let unknown = value["unknown_effects"].bool,
              receiptTotal == receipts.count || truncated,
              !receipts.contains(where: \.unknown) || unknown else { throw RPCFailure.invalidResponse }
        self.index = index; agentID = agent; agentRevision = revision; sessionID = session; runID = run
        maxSteps = steps; self.status = status; self.phase = phase; self.outcome = outcome
        answer = value["answer"].string; self.answerTruncated = answerTruncated; self.answerBytes = answerBytes
        answerDigest = value["answer_sha256"].string
        receiptsTruncated = truncated; receiptsTotal = receiptTotal; unknownEffects = unknown
        self.settled = settled
    }
}

public struct DelegationSnapshot: Identifiable, Equatable, Sendable {
    public let id: String
    public let parentRunID: String
    public let scope: SessionScope
    public let objective: String
    public let status: String
    public let maxSteps: Int
    public let reservedSteps: Int
    public let timeoutSeconds: Int
    public let parentSettled: Bool
    public let parentOutcome: String
    public let children: [DelegatedChildSnapshot]
    public let errorCode: String?
    public let snapshotTruncated: Bool
    public var settled: Bool { parentSettled && children.allSatisfy(\.settled) }
    public var params: [String: RPCValue] {
        var value = scope.params; value["parent_run_id"] = .string(parentRunID); value["goal_id"] = .string(id)
        return value
    }
    public init(_ value: RPCValue, expectedScope: SessionScope) throws {
        guard let id = value["goal_id"].string, ManagementValidation.identifier(id),
              let run = value["parent_run_id"].string, ManagementValidation.identifier(run),
              value["parent_session_id"].string == expectedScope.sessionID,
              value["temporary"].bool == expectedScope.temporary,
              let objective = value["objective"].string, ManagementValidation.text(objective, limit: 512),
              let status = value["status"].string,
              ["running", "cancelling", "completed", "failed", "cancelled", "interrupted", "unknown"].contains(status),
              let steps = value["max_steps"].integer, (1...100).contains(steps),
              let reserved = value["reserved_steps"].integer, reserved > 0, reserved <= steps,
              let timeout = value["timeout_seconds"].integer, (1...3600).contains(timeout),
              let parentSettled = value["parent_settled"].bool,
              let parentOutcome = value["parent_outcome"].string,
              ["pending", "known", "unknown"].contains(parentOutcome), parentSettled == (parentOutcome != "pending"),
              value["permits_replay"].bool == false else { throw RPCFailure.invalidResponse }
        children = try ManagementValidation.rows(value["children"], limit: 4, parse: DelegatedChildSnapshot.init)
        guard Set(children.map(\.runID)).count == children.count,
              Set(children.map(\.sessionID)).count == children.count,
              Set(children.map(\.index)).count == children.count,
              children.allSatisfy({ $0.sessionID != expectedScope.sessionID && $0.runID != run }),
              children.reduce(0, { $0 + $1.maxSteps }) <= reserved,
              !(status == "completed" && (children.isEmpty || children.contains(where: { $0.status != "completed" })))
        else { throw RPCFailure.invalidResponse }
        self.id = id; parentRunID = run; scope = expectedScope; self.objective = objective; self.status = status
        maxSteps = steps; reservedSteps = reserved; timeoutSeconds = timeout; self.parentSettled = parentSettled
        self.parentOutcome = parentOutcome
        errorCode = value["error_code"].string
        guard let truncated = value["snapshot_truncated"].bool else { throw RPCFailure.invalidResponse }
        snapshotTruncated = truncated
        guard value["settled"].bool == settled else { throw RPCFailure.invalidResponse }
        if status == "completed", children.reduce(0, { $0 + $1.maxSteps }) != reserved { throw RPCFailure.invalidResponse }
        if children.contains(where: { $0.answerTruncated || $0.receiptsTruncated || $0.receipts.contains(where: \.outputTruncated) }), !snapshotTruncated {
            throw RPCFailure.invalidResponse
        }
    }
    public func follows(_ previous: DelegationSnapshot) -> Bool {
        id == previous.id && scope == previous.scope && parentRunID == previous.parentRunID
        && objective == previous.objective && maxSteps == previous.maxSteps
        && reservedSteps == previous.reservedSteps && timeoutSeconds == previous.timeoutSeconds
        && (!previous.parentSettled || (parentSettled && parentOutcome == previous.parentOutcome))
        && (!previous.settled || (settled && status == previous.status)) && previous.children.allSatisfy { prior in
            children.contains { $0.runID == prior.runID && $0.sessionID == prior.sessionID
                && $0.agentID == prior.agentID && $0.agentRevision == prior.agentRevision
                && $0.index == prior.index && $0.maxSteps == prior.maxSteps
                && (!prior.unknownEffects || $0.unknownEffects)
                && (!prior.settled || ($0.settled && $0.status == prior.status && $0.outcome == prior.outcome
                    && $0.answerBytes == prior.answerBytes && $0.answerDigest == prior.answerDigest
                    && $0.receiptsTotal == prior.receiptsTotal)) }
        }
    }
}
