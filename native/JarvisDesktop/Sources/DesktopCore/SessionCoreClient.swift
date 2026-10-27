import Foundation

public struct SessionCoreOperation: Equatable, Sendable {
    public let id: String
    public let kind: String
    public let context: SessionContextSnapshot?
    public let branch: SessionBranchSnapshot?
    public let isHistoricalObservation: Bool
}

public final class SessionCoreClient: Sendable {
    public typealias Request = MaintenanceClient.Request
    private let request: Request
    private let closeTransport: @Sendable () async -> Void
    public init(transport: LocalRPCClient) {
        request = { try await transport.call($0, params: $1) }
        closeTransport = { await transport.close() }
    }
    public init(request: @escaping Request, close: @escaping @Sendable () async -> Void = {}) {
        self.request = request; closeTransport = close
    }
    public func close() async { await closeTransport() }
    public func context(_ scope: SessionScope) async throws -> SessionContextSnapshot {
        try await SessionContextSnapshot(request("sessions.context", scope.params)["context"])
    }
    public func compact(_ scope: SessionScope, revision: Int, operationID: String,
                        budget: Int = 12000, keepRecent: Int = 12,
                        decisions: [String] = []) async throws -> SessionCoreOperation {
        guard revision > 0, ManagementValidation.identifier(operationID), (4096...16000).contains(budget),
              (1...40).contains(keepRecent), decisions.count <= 16,
              decisions.allSatisfy({ ManagementValidation.text($0, limit: 1024) }) else { throw RPCFailure.configuration }
        var params = scope.params
        params.merge(["expected_revision": .number(Double(revision)), "operation_id": .string(operationID),
                      "budget_bytes": .number(Double(budget)), "keep_recent": .number(Double(keepRecent))]) { _, new in new }
        if !decisions.isEmpty { params["retained_decisions"] = .array(decisions.map(RPCValue.string)) }
        let value = try await request("sessions.compact", params)
        let context = try SessionContextSnapshot(value["context"])
        guard value["session"]["session_id"].string == scope.sessionID,
              value["session"]["temporary"].bool == scope.temporary,
              context.revision > revision, context.checkpoint?.sourceRevision == revision else { throw RPCFailure.invalidResponse }
        return SessionCoreOperation(id: operationID, kind: "compact", context: context, branch: nil, isHistoricalObservation: false)
    }
    public func rewind(_ scope: SessionScope, revision: Int, until: Int, operationID: String,
                       title: String) async throws -> SessionCoreOperation {
        guard revision > 0, until > 0, until <= revision, ManagementValidation.identifier(operationID),
              ManagementValidation.text(title, limit: 512) else { throw RPCFailure.configuration }
        var params = scope.params
        params.merge(["expected_revision": .number(Double(revision)), "operation_id": .string(operationID),
                      "until_sequence": .number(Double(until)), "title": .string(title)]) { _, new in new }
        let value = try await request("sessions.rewind", params)
        let branch = try SessionBranchSnapshot(session: value["session"], lineage: value["lineage"], source: scope)
        guard value["lineage"]["kind"].string == "rewind", branch.sourceRevision == revision,
              branch.throughSequence == until else { throw RPCFailure.invalidResponse }
        return SessionCoreOperation(id: operationID, kind: "rewind", context: nil, branch: branch, isHistoricalObservation: false)
    }
    public func fork(_ scope: SessionScope, revision: Int, operationID: String, title: String) async throws -> SessionCoreOperation {
        guard revision >= 0, ManagementValidation.identifier(operationID), ManagementValidation.text(title, limit: 512) else { throw RPCFailure.configuration }
        var params = scope.params; params["expected_revision"] = .number(Double(revision))
        params["operation_id"] = .string(operationID); params["title"] = .string(title)
        let value = try await request("sessions.fork", params)
        let branch = try SessionBranchSnapshot(session: value["session"], lineage: value["lineage"], source: scope)
        guard value["lineage"]["kind"].string == "fork", branch.sourceRevision == revision,
              branch.throughSequence == revision else { throw RPCFailure.invalidResponse }
        return SessionCoreOperation(id: operationID, kind: "fork", context: nil, branch: branch, isHistoricalObservation: false)
    }
    public func operation(_ scope: SessionScope, id: String, kind: String) async throws -> SessionCoreOperation? {
        guard ManagementValidation.identifier(id), ["compact", "rewind", "fork"].contains(kind) else { throw RPCFailure.configuration }
        var params = scope.params; params["operation_id"] = .string(id)
        let value = try await request("sessions.operation", params)
        guard value["operation"] != .null else { return nil }
        let record = value["operation"]
        return try parseOperation(record, scope: scope, id: id, kind: kind)
    }
    public func operations(_ scope: SessionScope, offset: Int = 0, limit: Int = 20) async throws -> (operations: [SessionCoreOperation], hasMore: Bool, nextOffset: Int) {
        guard offset >= 0, (1...100).contains(limit) else { throw RPCFailure.configuration }
        var params = scope.params; params["offset"] = .number(Double(offset)); params["limit"] = .number(Double(limit))
        let value = try await request("sessions.operations", params)
        let rows = try ManagementValidation.rows(value["operations"], limit: limit) { record in
            guard let id = record["operation_id"].string, let kind = record["operation_kind"].string else { throw RPCFailure.invalidResponse }
            return try parseOperation(record, scope: scope, id: id, kind: kind)
        }
        guard Set(rows.map(\.id)).count == rows.count,
              let more = value["has_more"].bool,
              more ? (value["next_offset"].integer == offset + rows.count && rows.count == limit)
                   : value["next_offset"] == .null else { throw RPCFailure.invalidResponse }
        return (rows, more, offset + rows.count)
    }
    private func parseOperation(_ record: RPCValue, scope: SessionScope, id: String, kind: String) throws -> SessionCoreOperation {
        guard ManagementValidation.identifier(id), ["compact", "rewind", "fork"].contains(kind) else { throw RPCFailure.invalidResponse }
        guard record["operation_id"].string == id, record["operation_kind"].string == kind,
              record["recorded"].bool == true, record["historical_ack_established"].bool == false,
              record["permits_replay"].bool == false else { throw RPCFailure.invalidResponse }
        if kind == "compact" {
            guard record["result"]["session"]["session_id"].string == scope.sessionID,
                  record["result"]["session"]["temporary"].bool == scope.temporary else { throw RPCFailure.invalidResponse }
            let context = try SessionContextSnapshot(record["result"]["context"])
            return SessionCoreOperation(id: id, kind: kind, context: context, branch: nil, isHistoricalObservation: true)
        }
        guard record["result"]["lineage"]["kind"].string == kind else { throw RPCFailure.invalidResponse }
        let branch = try SessionBranchSnapshot(session: record["result"]["session"], lineage: record["result"]["lineage"], source: scope)
        return SessionCoreOperation(id: id, kind: kind, context: nil, branch: branch, isHistoricalObservation: true)
    }
}
