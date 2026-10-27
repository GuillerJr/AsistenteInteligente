import Foundation

public final class DelegationClient: Sendable {
    public typealias Request = MaintenanceClient.Request
    private let request: Request
    private let closeTransport: @Sendable () async -> Void
    public init(transport: LocalRPCClient) {
        request = { try await transport.call($0, params: $1) }; closeTransport = { await transport.close() }
    }
    public init(request: @escaping Request, close: @escaping @Sendable () async -> Void = {}) {
        self.request = request; closeTransport = close
    }
    public func close() async { await closeTransport() }
    public func start(scope: SessionScope, objective: String, children: [DelegatedTaskSpec], requestID: String,
                      maxSteps: Int, timeout: Int) async throws -> DelegationSnapshot {
        guard ManagementValidation.identifier(requestID), ManagementValidation.text(objective, limit: 512),
              (1...4).contains(children.count), children.allSatisfy(\.valid),
              (1...100).contains(maxSteps), children.reduce(0, { $0 + $1.maxSteps }) <= maxSteps,
              (1...3600).contains(timeout) else { throw RPCFailure.configuration }
        var params = scope.params
        params.merge(["request_id": .string(requestID), "objective": .string(objective),
            "max_steps": .number(Double(maxSteps)), "timeout_seconds": .number(Double(timeout)),
            "children": .array(children.map(\.params))]) { _, new in new }
        let result = try await DelegationSnapshot(request("delegations.start", params)["goal"], expectedScope: scope)
        guard result.id == requestID, result.parentRunID == requestID, result.objective == objective,
              result.maxSteps == maxSteps, result.timeoutSeconds == timeout,
              result.reservedSteps == children.reduce(0, { $0 + $1.maxSteps }),
              result.children.allSatisfy({ row in row.index < children.count
                  && row.agentID == children[row.index].agentID && row.agentRevision == children[row.index].agentRevision
                  && row.maxSteps == children[row.index].maxSteps }) else { throw RPCFailure.invalidResponse }
        return result
    }
    public func get(_ goal: DelegationSnapshot) async throws -> DelegationSnapshot {
        let value = try await DelegationSnapshot(request("delegations.get", goal.params)["goal"], expectedScope: goal.scope)
        guard value.follows(goal) else { throw RPCFailure.invalidResponse }; return value
    }
    public func get(scope: SessionScope, parentRunID: String, goalID: String) async throws -> DelegationSnapshot {
        guard ManagementValidation.identifier(parentRunID), ManagementValidation.identifier(goalID) else { throw RPCFailure.configuration }
        var params = scope.params; params["parent_run_id"] = .string(parentRunID); params["goal_id"] = .string(goalID)
        let value = try await DelegationSnapshot(request("delegations.get", params)["goal"], expectedScope: scope)
        guard value.id == goalID, value.parentRunID == parentRunID else { throw RPCFailure.invalidResponse }; return value
    }
    public func lookup(scope: SessionScope, requestID: String) async throws -> DelegationSnapshot? {
        guard ManagementValidation.identifier(requestID) else { throw RPCFailure.configuration }
        var params = scope.params; params["request_id"] = .string(requestID)
        let value = try await request("delegations.lookup", params)
        guard value["permits_replay"].bool == false, value["historical_ack_established"].bool == false else { throw RPCFailure.invalidResponse }
        if value["goal"] == .null { return nil }
        let goal = try DelegationSnapshot(value["goal"], expectedScope: scope)
        guard goal.id == requestID, goal.parentRunID == requestID else { throw RPCFailure.invalidResponse }; return goal
    }
    public func list(scope: SessionScope) async throws -> [DelegationSnapshot] {
        try await page(scope: scope).goals
    }
    public func page(scope: SessionScope, offset: Int = 0, limit: Int = 20) async throws -> (goals: [DelegationSnapshot], hasMore: Bool, nextOffset: Int) {
        guard offset >= 0, (1...100).contains(limit) else { throw RPCFailure.configuration }
        var params = scope.params; params["limit"] = .number(Double(limit)); params["offset"] = .number(Double(offset))
        let value = try await request("delegations.list", params)
        let goals = try ManagementValidation.rows(value["goals"], limit: limit) { try DelegationSnapshot($0, expectedScope: scope) }
        guard Set(goals.map(\.id)).count == goals.count, let more = value["has_more"].bool,
              value["next_offset"].integer == offset + goals.count,
              !more || !goals.isEmpty else { throw RPCFailure.invalidResponse }
        return (goals, more, offset + goals.count)
    }
    public func cancel(_ goal: DelegationSnapshot) async throws -> Bool {
        let value = try await request("delegations.cancel", goal.params)
        guard value["goal_id"].string == goal.id, let accepted = value["accepted"].bool,
              value["settled"].bool != nil else { throw RPCFailure.invalidResponse }; return accepted
    }
    public func steer(_ goal: DelegationSnapshot, child: DelegatedChildSnapshot, message: String, requestID: String) async throws -> String {
        guard goal.children.contains(child), !child.settled, ManagementValidation.text(message, limit: 8000),
              ManagementValidation.identifier(requestID) else { throw RPCFailure.configuration }
        var params = goal.params
        params["child_run_id"] = .string(child.runID); params["message"] = .string(message); params["request_id"] = .string(requestID)
        let value = try await request("delegations.steer", params)
        guard value["run_id"].string == child.runID, value["request_id"].string == requestID,
              let status = value["status"].string, ["accepted", "rejected", "unknown", "failed"].contains(status),
              value["permits_replay"].bool == false else { throw RPCFailure.invalidResponse }; return status
    }
    public func approvals(_ goal: DelegationSnapshot) async throws -> [ApprovalOperation] {
        var operations: [ApprovalOperation] = []
        for child in goal.children where !child.settled {
            let scope = SessionScope(sessionID: child.sessionID, temporary: goal.scope.temporary)
            var params = scope.params; params["run_id"] = .string(child.runID)
            let value = try await request("approvals.list", params)
            operations += try ManagementValidation.rows(value["approvals"], limit: 32) {
                try ApprovalOperation($0, expectedScope: scope, expectedRunID: child.runID)
            }
        }
        guard Set(operations.map(\.id)).count == operations.count else { throw RPCFailure.invalidResponse }
        return operations
    }
    public func decide(_ operation: ApprovalOperation, goal: DelegationSnapshot, allow: Bool) async throws -> Bool {
        guard goal.children.contains(where: { $0.runID == operation.runID && $0.sessionID == operation.scope.sessionID && !$0.settled }),
              operation.scope.temporary == goal.scope.temporary, !allow || operation.expiresAt > Date() else { throw RPCFailure.configuration }
        let value = try await request("approvals.decide", operation.decision(allow: allow))
        guard let accepted = value["accepted"].bool else { throw RPCFailure.invalidResponse }; return accepted
    }
}
