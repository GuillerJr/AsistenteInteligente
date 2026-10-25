import Foundation

public struct ConversationMessage: Identifiable, Equatable, Sendable {
    public let id: String
    public let sequence: Int
    public let role: String
    public let text: String

    public init(_ value: RPCValue, scope: SessionScope) throws {
        guard let id = value["message_id"].string, ManagementValidation.identifier(id),
              value["session_id"].string == scope.sessionID,
              let sequence = value["sequence"].integer, sequence > 0,
              let role = value["role"].string, ["user", "assistant", "system"].contains(role),
              let text = value["text"].string, text.utf8.count <= 512000, !text.contains("\0") else {
            throw RPCFailure.invalidResponse
        }
        self.id = id; self.sequence = sequence; self.role = role; self.text = text
    }
}

public struct ConversationRun: Equatable, Sendable {
    public let id: String
    public let status: String
    public let outcome: String
    public let receipts: [ActionReceipt]
    public let errorCode: String?
    public var active: Bool { ["queued", "running", "waiting_approval", "cancelling", "cleaning"].contains(status) }

    public init(_ value: RPCValue, expectedID: String) throws {
        let activeStates = ["queued", "running", "waiting_approval", "cancelling", "cleaning"]
        guard value["run_id"].string == expectedID, ManagementValidation.identifier(expectedID),
              let status = value["status"].string,
              (activeStates + ["completed", "failed", "cancelled", "interrupted"]).contains(status),
              let outcome = value["outcome"].string, ["pending", "known", "unknown"].contains(outcome),
              activeStates.contains(status) == (outcome == "pending"), status != "completed" || outcome == "known" else {
            throw RPCFailure.invalidResponse
        }
        let receipts = try ManagementValidation.rows(value["action_receipts"], limit: 256, parse: ActionReceipt.init)
        guard Set(receipts.map(\.id)).count == receipts.count else { throw RPCFailure.invalidResponse }
        if value["error"] != .null {
            guard let error = value["error"].string, error.utf8.count <= 512 else { throw RPCFailure.invalidResponse }
        }
        id = expectedID; self.status = status; self.outcome = outcome; self.receipts = receipts; errorCode = value["error"].string
    }
}

public struct ConversationSnapshot: Equatable, Sendable {
    public let scope: SessionScope
    public let messages: [ConversationMessage]
    public let latestRun: ConversationRun?
}

/// Only three scoped read methods. The caller's selected conversation is the
/// sole source of scope; no model dispatch, approval or mutation is available.
public final class ConversationReader: Sendable {
    private let transport: LocalRPCClient
    public init(transport: LocalRPCClient) { self.transport = transport }

    public func read(_ scope: SessionScope) async throws -> ConversationSnapshot {
        var latestID: String?
        var offset = 0
        var runsComplete = false
        for _ in 0..<20 {
            var params = scope.params; params["offset"] = .number(Double(offset))
            let result = try await transport.call("runs.list", params: params)
            guard let rows = result["runs"].array, rows.count <= 100,
                  let more = result["has_more"].bool, let next = result["next_offset"].integer else { throw RPCFailure.invalidResponse }
            for row in rows {
                guard let id = row["run_id"].string, ManagementValidation.identifier(id) else { throw RPCFailure.invalidResponse }
                latestID = id
            }
            if !more { runsComplete = true; break }
            guard next > offset else { throw RPCFailure.invalidResponse }
            offset = next
        }
        guard runsComplete else { throw RPCFailure.oversized }
        let run: ConversationRun?
        if let latestID {
            var params = scope.params; params["run_id"] = .string(latestID)
            let result = try await transport.call("runs.get", params: params)
            run = try ConversationRun(result["run"], expectedID: latestID)
        } else { run = nil }
        var messages: [ConversationMessage] = []
        var after = 0
        var bytes = 0
        var messagesComplete = false
        for _ in 0..<50 {
            var params = scope.params; params["after"] = .number(Double(after))
            let result = try await transport.call("sessions.messages", params: params)
            let page = try ManagementValidation.rows(result["messages"], limit: 50) { try ConversationMessage($0, scope: scope) }
            guard let more = result["has_more"].bool else { throw RPCFailure.invalidResponse }
            for message in page {
                guard message.sequence > (messages.last?.sequence ?? after) else { throw RPCFailure.invalidResponse }
                bytes += message.text.utf8.count
                guard bytes <= 16 * 1024 * 1024 else { throw RPCFailure.oversized }
                messages.append(message)
            }
            if !more { messagesComplete = true; break }
            guard let next = result["next_after"].integer, next > after, next == messages.last?.sequence else { throw RPCFailure.invalidResponse }
            after = next
        }
        guard messagesComplete, Set(messages.map(\.id)).count == messages.count else { throw RPCFailure.oversized }
        return ConversationSnapshot(scope: scope, messages: messages, latestRun: run)
    }
}

/// One read at a time; a changed selection or cancelled monitor invalidates
/// the response even if the old socket operation finishes later.
@MainActor public final class ConversationRefresh {
    private let reader: ConversationReader
    private var scope: SessionScope?
    private var generation = 0
    private var reading = false
    public init(reader: ConversationReader) { self.reader = reader }
    public func setScope(_ scope: SessionScope?) {
        guard self.scope != scope else { return }
        generation += 1; self.scope = scope
    }
    public func refresh() async throws -> ConversationSnapshot? {
        guard !reading, !Task.isCancelled, let scope else { return nil }
        reading = true
        let version = generation
        defer { reading = false }
        do {
            let snapshot = try await reader.read(scope)
            guard version == generation, !Task.isCancelled else { return nil }
            return snapshot
        } catch {
            if version != generation || Task.isCancelled { return nil }
            throw error
        }
    }
}
