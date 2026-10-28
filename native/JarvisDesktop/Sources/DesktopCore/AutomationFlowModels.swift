import Foundation

public enum AutomationWebhookInput {
    public static func validSecret(_ value: String) -> Bool {
        (32...64).contains(value.utf8.count) && value.utf8.allSatisfy { (33...126).contains($0) }
    }
}

public struct AutomationStep: Equatable, Sendable {
    public let prompt: String
    public let pauseAfter: Bool
    public init(_ value: RPCValue) throws {
        guard let prompt = value["prompt"].string, ManagementValidation.text(prompt, limit: 8000),
              let pause = value["pause_after"].bool else { throw RPCFailure.invalidResponse }
        self.prompt = prompt; pauseAfter = pause
    }
    public var value: RPCValue { .object(["prompt": .string(prompt), "pause_after": .bool(pauseAfter)]) }
    public static func parse(_ value: RPCValue) throws -> [AutomationStep] {
        let rows = try ManagementValidation.rows(value, limit: 16, parse: AutomationStep.init)
        guard !rows.isEmpty else { throw RPCFailure.invalidResponse }; return rows
    }
}

public struct AutomationHook: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let event: String
    public let agentID: String
    public let workspaceID: String
    public let revision: Int
    public let enabled: Bool
    public let steps: [AutomationStep]
    public init(_ value: RPCValue) throws {
        guard let id = value["hook_id"].string, ManagementValidation.identifier(id),
              let title = value["title"].string, ManagementValidation.text(title, limit: 512),
              let event = value["event"].string, Self.validEvent(event),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              let revision = value["revision"].integer, revision > 0,
              let enabled = value["enabled"].bool else { throw RPCFailure.invalidResponse }
        steps = try AutomationStep.parse(value["steps"])
        self.id = id; self.title = title; self.event = event; agentID = agent; workspaceID = workspace
        self.revision = revision; self.enabled = enabled
    }
    public static func validEvent(_ event: String) -> Bool {
        event.range(of: "^[a-z][a-z0-9_.-]{0,63}$", options: .regularExpression) != nil
    }
    public var revisionParams: [String: RPCValue] { ["hook_id": .string(id), "expected_revision": .number(Double(revision))] }
    public var reviewValue: RPCValue { .object(revisionParams.merging(["title": .string(title), "event": .string(event), "agent_id": .string(agentID), "workspace_id": .string(workspaceID), "enabled": .bool(enabled), "steps": .array(steps.map(\.value))]) { _, value in value }) }
}

public struct AutomationStepReceipt: Identifiable, Equatable, Sendable {
    public let id: String
    public let stepIndex: Int
    public let runID: String?
    public let status: String
    public let outcome: String
    public init(_ value: RPCValue) throws {
        guard let claim = value["claim_id"].string, ManagementValidation.identifier(claim),
              let index = value["step_index"].integer, (0..<16).contains(index),
              let status = value["status"].string, ["completed", "failed", "cancelled", "interrupted"].contains(status),
              let outcome = value["outcome"].string, ["known", "unknown"].contains(outcome),
              !(status == "completed" && outcome != "known") else { throw RPCFailure.invalidResponse }
        if value["run_id"] != .null {
            guard let run = value["run_id"].string, ManagementValidation.identifier(run) else { throw RPCFailure.invalidResponse }
        }
        id = claim; stepIndex = index; runID = value["run_id"].string; self.status = status; self.outcome = outcome
    }
}

public struct AutomationFlow: Identifiable, Equatable, Sendable {
    public let id: String
    public let hookID: String
    public let hookRevision: Int
    public let title: String
    public let agentID: String
    public let workspaceID: String
    public let revision: Int
    public let status: String
    public let outcome: String
    public let cursor: Int
    public let steps: [AutomationStep]
    public let runIDs: [String]
    public let receipts: [AutomationStepReceipt]
    public let expiresAt: Date
    public let errorCode: String?
    public init(_ value: RPCValue) throws {
        guard let id = value["flow_id"].string, ManagementValidation.identifier(id),
              let hook = value["hook_id"].string, ManagementValidation.identifier(hook),
              let hookRevision = value["hook_revision"].integer, hookRevision > 0,
              let title = value["title"].string, ManagementValidation.text(title, limit: 512),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              let revision = value["revision"].integer, revision > 0,
              let status = value["status"].string, ["queued", "running", "paused", "completed", "failed", "cancelled", "interrupted"].contains(status),
              let outcome = value["outcome"].string, ["pending", "known", "unknown"].contains(outcome),
              let cursor = value["cursor"].integer, cursor >= 0,
              let expires = ManagementValidation.timestamp(value["expires_at"])
        else { throw RPCFailure.invalidResponse }
        let steps = try AutomationStep.parse(value["steps"])
        guard cursor <= steps.count,
              !(status == "completed" && (outcome != "known" || cursor != steps.count)),
              !(["queued", "running"].contains(status) && outcome != "pending"),
              !(["paused", "completed"].contains(status) && outcome != "known"),
              !(["failed", "cancelled", "interrupted"].contains(status) && outcome == "pending"),
              !(["queued", "running", "paused"].contains(status) && cursor == steps.count),
              let runs = value["run_ids"].array, runs.count <= steps.count else { throw RPCFailure.invalidResponse }
        let parsedRuns: [String] = try runs.map { item in
            guard let id = item.string, ManagementValidation.identifier(id) else { throw RPCFailure.invalidResponse }; return id
        }
        guard Set(parsedRuns).count == parsedRuns.count else { throw RPCFailure.invalidResponse }
        let parsedReceipts = try ManagementValidation.rows(value["receipts"], limit: 16, parse: AutomationStepReceipt.init)
        guard Set(parsedReceipts.map(\.id)).count == parsedReceipts.count,
              parsedReceipts.enumerated().allSatisfy({ $0.offset == $0.element.stepIndex }),
              parsedReceipts.allSatisfy({ $0.runID == nil || parsedRuns.contains($0.runID!) }),
              [cursor, cursor + 1].contains(parsedReceipts.count),
              !(["queued", "running", "paused", "completed"].contains(status) && parsedReceipts.count != cursor) else { throw RPCFailure.invalidResponse }
        runIDs = parsedRuns; receipts = parsedReceipts
        self.id = id; hookID = hook; self.hookRevision = hookRevision; self.title = title; agentID = agent
        workspaceID = workspace; self.revision = revision; self.status = status; self.outcome = outcome
        self.cursor = cursor; self.steps = steps; expiresAt = Date(timeIntervalSince1970: expires)
        errorCode = value["error_code"].string
    }
    public var revisionParams: [String: RPCValue] { ["flow_id": .string(id), "expected_revision": .number(Double(revision))] }
    public var canResume: Bool { status == "paused" && outcome != "unknown" && expiresAt > Date() }
    public var canPause: Bool { ["queued", "running"].contains(status) }
    public var canCancel: Bool { ["queued", "running", "paused"].contains(status) }
}

public struct AutomationListener: Equatable, Sendable {
    public let running: Bool
    public let pendingCleanup: Bool
    public let url: String?
    public let hookID: String?
    public init(_ value: RPCValue) throws {
        guard let running = value["running"].bool,
              value["secret"] == .null, value["token"] == .null,
              value["pending_cleanup"] == .null || value["pending_cleanup"].bool != nil else { throw RPCFailure.invalidResponse }
        self.running = running; pendingCleanup = value["pending_cleanup"].bool ?? false
        url = value["url"].string; hookID = value["hook_id"].string
        if running {
            guard let url, let components = URLComponents(string: url), components.scheme == "http",
                  components.host == "127.0.0.1", let port = components.port, (1...65535).contains(port),
                  components.user == nil, components.password == nil, components.query == nil, components.fragment == nil,
                  let hookID, ManagementValidation.identifier(hookID) else { throw RPCFailure.invalidResponse }
        } else {
            guard url == nil else { throw RPCFailure.invalidResponse }
            if let hookID { guard pendingCleanup, ManagementValidation.identifier(hookID) else { throw RPCFailure.invalidResponse } }
        }
    }
}

public struct AutomationSnapshot: Sendable {
    public let hooks: [AutomationHook]
    public let flows: [AutomationFlow]
    public let configurationHash: String
    public let running: Bool
    public let workspaceID: String
    public let webhook: AutomationListener
    public let errorCode: String?
    public let offset: Int
    public let nextOffset: Int?
    public let hasMore: Bool
    public init(_ value: RPCValue) throws {
        hooks = try ManagementValidation.rows(value["hooks"], limit: 32, parse: AutomationHook.init)
        flows = try ManagementValidation.rows(value["flows"], limit: 256, parse: AutomationFlow.init)
        guard let hash = value["configuration_hash"].string, ManagementValidation.hash(hash),
              let running = value["runner_running"].bool,
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              Set(hooks.map(\.id)).count == hooks.count, Set(flows.map(\.id)).count == flows.count else { throw RPCFailure.invalidResponse }
        configurationHash = hash; self.running = running; workspaceID = workspace
        webhook = try AutomationListener(value["webhook"])
        errorCode = value["error"].string
        guard value["offset"] == .null || value["offset"].integer != nil,
              value["has_more"] == .null || value["has_more"].bool != nil else { throw RPCFailure.invalidResponse }
        offset = value["offset"].integer ?? 0; hasMore = value["has_more"].bool ?? false
        nextOffset = value["next_offset"].integer
        guard (0...256).contains(offset), hasMore == (nextOffset != nil),
              !hasMore || (!flows.isEmpty && nextOffset == offset + flows.count && nextOffset! <= 256),
              value["next_offset"] == .null || nextOffset != nil else { throw RPCFailure.invalidResponse }
    }
}
