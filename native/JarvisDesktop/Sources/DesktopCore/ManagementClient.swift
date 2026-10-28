import Foundation

/// This host-facing client is separate from inference and tool approvals. Lists
/// never start a runner, import skills, execute processes or mutate profiles.
public final class ManagementClient: Sendable {
    public typealias Request = @Sendable (String, [String: RPCValue]) async throws -> RPCValue
    private let request: Request
    private let finish: @Sendable () async -> Void
    public init(transport: LocalRPCClient) {
        request = { try await transport.call($0, params: $1) }; finish = { await transport.close() }
    }
    public init(request: @escaping Request, close: @escaping @Sendable () async -> Void = {}) {
        self.request = request; finish = close
    }
    public func close() async { await finish() }
    func call(_ method: String, params: [String: RPCValue] = [:]) async throws -> RPCValue {
        try await request(method, params)
    }
    public func agents() async throws -> [AgentDescriptor] {
        let result = try await call("agents.list")
        let agents = try ManagementValidation.rows(result["agents"], limit: 32, parse: AgentDescriptor.init)
        guard Set(agents.map(\.id)).count == agents.count, agents.contains(where: { $0.id == "jarvis" }) else { throw RPCFailure.invalidResponse }
        return agents
    }
    public func skills(agentID: String) async throws -> [SkillDescriptor] {
        let result = try await call("skills.list", params: ["agent_id": .string(agentID)])
        let rows = try ManagementValidation.rows(result["skills"], limit: 128, parse: SkillDescriptor.init)
        guard rows.allSatisfy({ $0.agentID == agentID }), Set(rows.map(\.id)).count == rows.count else { throw RPCFailure.invalidResponse }
        return rows
    }
    public func inspect(path: String) async throws -> SkillPreviewSnapshot {
        let result = try await call("skills.inspect", params: ["path": .string(path)])
        let preview = try SkillPreviewSnapshot(result["preview"])
        guard preview.sourcePath == path else { throw RPCFailure.invalidResponse }
        return preview
    }
    public func skillText(_ skill: SkillDescriptor) async throws -> String {
        let result = try await call("skills.get", params: ["agent_id": .string(skill.agentID), "skill_id": .string(skill.id)])
        guard try SkillDescriptor(result["skill"]) == skill, let text = result["text"].string,
              ManagementValidation.text(text, limit: 32768), ManagementValidation.digest(text) == skill.hash else { throw RPCFailure.invalidResponse }
        return text
    }
    public func schedules() async throws -> SchedulerSnapshot { try await SchedulerSnapshot(call("schedules.list")) }
    public func automation(offset: Int = 0) async throws -> AutomationSnapshot {
        guard (0...256).contains(offset) else { throw RPCFailure.configuration }
        let snapshot = try await AutomationSnapshot(call("automation.list", params: ["offset": .number(Double(offset))]))
        guard snapshot.offset == offset else { throw RPCFailure.invalidResponse }; return snapshot
    }
    public func mutateAutomation(_ method: String, params: [String: RPCValue]) async throws -> RPCValue {
        guard ["automation.hooks.create", "automation.hooks.update", "automation.hooks.remove", "automation.hooks.emit",
               "automation.flows.pause", "automation.flows.resume", "automation.flows.cancel", "automation.start", "automation.stop",
               "automation.webhook.start", "automation.webhook.stop"].contains(method) else { throw RPCFailure.configuration }
        let result = try await call(method, params: params)
        switch method {
        case "automation.hooks.create", "automation.hooks.update":
            let hook = try AutomationHook(result["hook"])
            if let id = params["hook_id"]?.string, hook.id != id { throw RPCFailure.invalidResponse }
        case "automation.hooks.emit":
            let flows = try ManagementValidation.rows(result["flows"], limit: 256, parse: AutomationFlow.init)
            guard flows.allSatisfy({ $0.hookID == params["hook_id"]?.string }) else { throw RPCFailure.invalidResponse }
        case "automation.flows.pause", "automation.flows.resume", "automation.flows.cancel":
            let flow = try AutomationFlow(result["flow"])
            guard flow.id == params["flow_id"]?.string else { throw RPCFailure.invalidResponse }
        case "automation.hooks.remove":
            guard result["removed"].bool == true else { throw RPCFailure.invalidResponse }
        case "automation.start", "automation.stop":
            guard result["running"].bool == (method == "automation.start") else { throw RPCFailure.invalidResponse }
        default:
            let listener = try AutomationListener(result["listener"])
            guard listener.running == (method == "automation.webhook.start"),
                  method != "automation.webhook.start" || listener.hookID == params["hook_id"]?.string else { throw RPCFailure.invalidResponse }
        }
        return result
    }
    public func history(scheduleID: String) async throws -> [ScheduleOccurrenceSnapshot] {
        let result = try await call("schedules.history", params: ["schedule_id": .string(scheduleID)])
        let rows = try ManagementValidation.rows(result["occurrences"], limit: 100, parse: ScheduleOccurrenceSnapshot.init)
        guard rows.allSatisfy({ $0.scheduleID == scheduleID }), Set(rows.map(\.id)).count == rows.count else { throw RPCFailure.invalidResponse }
        return rows
    }
    public func processes(scope: SessionScope) async throws -> ProcessCatalogSnapshot {
        try await ProcessCatalogSnapshot(call("processes.list", params: scope.params))
    }
    public func poll(scope: SessionScope, processID: String, cursor: Int = 0) async throws -> ProcessSnapshot {
        var params = scope.params; params["process_id"] = .string(processID); params["cursor"] = .number(Double(cursor))
        let result = try await call("processes.poll", params: params)
        let process = try ProcessSnapshot(result["process"])
        guard process.id == processID else { throw RPCFailure.invalidResponse }
        return process
    }
    public func mutate(_ method: String, params: [String: RPCValue], successKey: String? = nil) async throws -> RPCValue {
        guard ["agents.create", "agents.update", "agents.delete", "skills.import", "skills.update", "skills.enable", "skills.remove", "skills.rollback", "skills.retire_version", "skills.import_library",
               "schedules.create", "schedules.update", "schedules.remove", "schedules.wake", "schedules.cancel", "scheduler.start", "scheduler.stop", "scheduler.suspend", "scheduler.resume"].contains(method) else {
            throw RPCFailure.configuration
        }
        let result = try await call(method, params: params)
        if let successKey, result[successKey].bool != true { throw RPCFailure.invalidResponse }
        return result
    }
}
