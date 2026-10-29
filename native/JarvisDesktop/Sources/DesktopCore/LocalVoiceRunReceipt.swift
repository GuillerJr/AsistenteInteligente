import Foundation

/// Voice consumes only the exact durable run requested by the host. Streaming
/// answer deltas and cancellation acknowledgements never satisfy this parser.
public struct LocalVoiceRunReceipt: Equatable, Sendable {
    public let runID: String
    public let status: String
    public let outcome: String
    public let answer: String?
    public let error: String?
    public let receipts: [ActionReceipt]
    private let hasError: Bool
    public var isSettled: Bool { ["completed", "failed", "cancelled", "interrupted"].contains(status) }
    public var requiresApproval: Bool { status == "waiting_approval" }

    public init(_ value: RPCValue, expectedRunID: String) throws {
        guard ManagementValidation.identifier(expectedRunID), let object = value.object,
              Set(object.keys) == ["run_id", "status", "outcome", "answer", "error", "error_type", "answer_source", "action_receipts"],
              value["run_id"] == .string(expectedRunID), let phase = value["status"].string,
              ["queued", "running", "waiting_approval", "cancelling", "cleaning", "completed", "failed", "cancelled", "interrupted"].contains(phase),
              let result = value["outcome"].string, ["pending", "known", "unknown"].contains(result),
              ["completed", "failed", "cancelled", "interrupted"].contains(phase) == (result != "pending"),
              let rows = value["action_receipts"].array, rows.count <= 256 else { throw RPCFailure.invalidResponse }
        func optionalText(_ key: String, limit: Int) throws -> String? {
            if value[key] == .null { return nil }
            guard let text = value[key].string, text.utf8.count <= limit, !text.contains("\0") else { throw RPCFailure.invalidResponse }
            return text
        }
        let text = try optionalText("answer", limit: 32000)
        let failure = try optionalText("error", limit: 4096)
        let failureType = try optionalText("error_type", limit: 512)
        let source = value["answer_source"].string
        guard value["answer_source"] == .null || source == "model" || source == "host",
              phase != "completed" || (result == "known" && text != nil && source != nil),
              phase != "interrupted" || result == "unknown"
        else { throw RPCFailure.invalidResponse }
        let parsed = try rows.map(ActionReceipt.init)
        runID = expectedRunID; status = phase; outcome = result; answer = text; error = failure; receipts = parsed
        hasError = failure != nil || failureType != nil
    }

    public var conversationReply: LocalVoiceConversationReply? {
        guard isSettled else { return nil }
        return LocalVoiceConversationReply(runID: runID, status: status, outcome: outcome,
            answer: answer ?? "", unconfirmedEffects: hasError || receipts.contains(where: \.unknown))
    }
}
