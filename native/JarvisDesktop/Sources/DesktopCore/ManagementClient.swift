import Foundation

/// This host-facing client is separate from inference and tool approvals. Lists
/// never start a runner, import skills, execute processes or mutate profiles.
public final class ManagementClient: Sendable {
    private let transport: LocalRPCClient
    public init(transport: LocalRPCClient) { self.transport = transport }
    public func close() async { await transport.close() }
    public func agents() async throws -> [AgentDescriptor] {
        let result = try await transport.call("agents.list")
        let agents = try ManagementValidation.rows(result["agents"], limit: 32, parse: AgentDescriptor.init)
        guard Set(agents.map(\.id)).count == agents.count, agents.contains(where: { $0.id == "jarvis" }) else { throw RPCFailure.invalidResponse }
        return agents
    }
    public func skills(agentID: String) async throws -> [SkillDescriptor] {
        let result = try await transport.call("skills.list", params: ["agent_id": .string(agentID)])
        let rows = try ManagementValidation.rows(result["skills"], limit: 128, parse: SkillDescriptor.init)
        guard rows.allSatisfy({ $0.agentID == agentID }), Set(rows.map(\.id)).count == rows.count else { throw RPCFailure.invalidResponse }
        return rows
    }
    public func inspect(path: String) async throws -> SkillPreviewSnapshot {
        let result = try await transport.call("skills.inspect", params: ["path": .string(path)])
        let preview = try SkillPreviewSnapshot(result["preview"])
        guard preview.sourcePath == path else { throw RPCFailure.invalidResponse }
        return preview
    }
    public func skillText(_ skill: SkillDescriptor) async throws -> String {
        let result = try await transport.call("skills.get", params: ["agent_id": .string(skill.agentID), "skill_id": .string(skill.id)])
        guard try SkillDescriptor(result["skill"]) == skill, let text = result["text"].string,
              ManagementValidation.text(text, limit: 32768), ManagementValidation.digest(text) == skill.hash else { throw RPCFailure.invalidResponse }
        return text
    }
    public func schedules() async throws -> SchedulerSnapshot { try await SchedulerSnapshot(transport.call("schedules.list")) }
    public func history(scheduleID: String) async throws -> [ScheduleOccurrenceSnapshot] {
        let result = try await transport.call("schedules.history", params: ["schedule_id": .string(scheduleID)])
        let rows = try ManagementValidation.rows(result["occurrences"], limit: 100, parse: ScheduleOccurrenceSnapshot.init)
        guard rows.allSatisfy({ $0.scheduleID == scheduleID }), Set(rows.map(\.id)).count == rows.count else { throw RPCFailure.invalidResponse }
        return rows
    }
    public func processes(scope: SessionScope) async throws -> ProcessCatalogSnapshot {
        try await ProcessCatalogSnapshot(transport.call("processes.list", params: scope.params))
    }
    public func poll(scope: SessionScope, processID: String, cursor: Int = 0) async throws -> ProcessSnapshot {
        var params = scope.params; params["process_id"] = .string(processID); params["cursor"] = .number(Double(cursor))
        let result = try await transport.call("processes.poll", params: params)
        let process = try ProcessSnapshot(result["process"])
        guard process.id == processID else { throw RPCFailure.invalidResponse }
        return process
    }
    public func mutate(_ method: String, params: [String: RPCValue], successKey: String? = nil) async throws -> RPCValue {
        guard ["agents.create", "agents.update", "agents.delete", "skills.import", "skills.update", "skills.enable", "skills.remove",
               "schedules.create", "schedules.update", "schedules.remove", "scheduler.start", "scheduler.stop"].contains(method) else {
            throw RPCFailure.configuration
        }
        let result = try await transport.call(method, params: params)
        if let successKey, result[successKey].bool != true { throw RPCFailure.invalidResponse }
        return result
    }
}
