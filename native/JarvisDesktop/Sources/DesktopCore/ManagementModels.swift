import CryptoKit
import Darwin
import Foundation

public enum ManagementValidation {
    public static func identifier(_ value: String, agent: Bool = false) -> Bool {
        if agent { return value.range(of: "^[a-z][a-z0-9_-]{0,63}$", options: .regularExpression) != nil }
        return value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
    public static func hash(_ value: String) -> Bool { MemoryDocument.isHash(value) }
    public static func digest(_ value: String) -> String { SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined() }
    public static func physicalPath(_ path: String) throws -> String {
        guard let resolved = realpath(path, nil) else { throw RPCFailure.configuration }
        defer { free(resolved) }
        return String(cString: resolved)
    }
    public static func text(_ value: String, limit: Int, empty: Bool = false) -> Bool {
        value.utf8.count <= limit && !value.contains("\0") && (empty || !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }
    public static func rows<T>(_ value: RPCValue, limit: Int, parse: (RPCValue) throws -> T) throws -> [T] {
        guard let rows = value.array, rows.count <= limit else { throw RPCFailure.invalidResponse }
        return try rows.map(parse)
    }
    public static func timestamp(_ value: RPCValue) -> Double? {
        guard case .number(let value) = value, value.isFinite, value >= 0, value < 253_402_214_400 else { return nil }
        return value
    }
    public static func json(_ value: RPCValue) -> String {
        guard let data = try? JSONEncoder().encode(value), let text = String(data: data, encoding: .utf8) else { return "" }
        return text
    }
}

public struct AgentDescriptor: Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let instructions: String
    public let revision: Int

    public init(_ value: RPCValue) throws {
        guard let id = value["agent_id"].string, ManagementValidation.identifier(id, agent: true),
              let name = value["name"].string, ManagementValidation.text(name, limit: 256),
              let instructions = value["instructions"].string, ManagementValidation.text(instructions, limit: 8192, empty: true),
              let revision = value["revision"].integer, revision > 0 else { throw RPCFailure.invalidResponse }
        self.id = id; self.name = name; self.instructions = instructions; self.revision = revision
    }
    public var revisionParams: [String: RPCValue] { ["agent_id": .string(id), "expected_revision": .number(Double(revision))] }
}

public struct SkillDescriptor: Identifiable, Equatable, Sendable {
    public let id: String
    public let agentID: String
    public let name: String
    public let description: String
    public let revision: Int
    public let hash: String
    public let enabled: Bool
    public let sourcePath: String
    public let resources: [String]

    public init(_ value: RPCValue) throws {
        guard let id = value["skill_id"].string,
              id.hasPrefix("skill_"), ManagementValidation.identifier(String(id.dropFirst(6))),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let name = value["name"].string, ManagementValidation.text(name, limit: 256),
              let description = value["description"].string, ManagementValidation.text(description, limit: 4096),
              let revision = value["revision"].integer, revision > 0,
              let hash = value["sha256"].string, ManagementValidation.hash(hash),
              let enabled = value["enabled"].bool,
              let source = value["source_path"].string, ManagementValidation.text(source, limit: 4096),
              value["source_verified"].bool == false,
              let resources = value["resources"].array, resources.count <= 128 else { throw RPCFailure.invalidResponse }
        self.resources = try resources.map { item in
            guard let text = item.string, ManagementValidation.text(text, limit: 512) else { throw RPCFailure.invalidResponse }
            return text
        }
        self.id = id; agentID = agent; self.name = name; self.description = description
        self.revision = revision; self.hash = hash; self.enabled = enabled; sourcePath = source
    }
    public var revisionParams: [String: RPCValue] {
        ["agent_id": .string(agentID), "skill_id": .string(id), "expected_revision": .number(Double(revision)), "expected_sha256": .string(hash)]
    }
}

public struct SkillPreviewSnapshot: Identifiable, Equatable, Sendable {
    public let name: String
    public let description: String
    public let text: String
    public let hash: String
    public let sourcePath: String
    public let resources: [String]
    public var id: String { sourcePath + hash }

    public init(_ value: RPCValue) throws {
        guard let name = value["name"].string, ManagementValidation.text(name, limit: 256),
              let description = value["description"].string, ManagementValidation.text(description, limit: 4096),
              let text = value["text"].string, ManagementValidation.text(text, limit: 32768),
              let hash = value["sha256"].string, ManagementValidation.hash(hash), ManagementValidation.digest(text) == hash,
              let path = value["source_path"].string, path.hasPrefix("/"), ManagementValidation.text(path, limit: 4096),
              let resources = value["resources"].array, resources.count <= 128 else { throw RPCFailure.invalidResponse }
        self.resources = try resources.map { item in
            guard let text = item.string, ManagementValidation.text(text, limit: 512) else { throw RPCFailure.invalidResponse }
            return text
        }
        self.name = name; self.description = description; self.text = text; self.hash = hash; sourcePath = path
    }
}

public struct ScheduleDescriptor: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let prompt: String
    public let agentID: String
    public let workspaceID: String
    public let spec: RPCValue
    public let enabled: Bool
    public let revision: Int
    public let nextAt: Date?

    public init(_ value: RPCValue) throws {
        guard let id = value["schedule_id"].string, ManagementValidation.identifier(id),
              let title = value["title"].string, ManagementValidation.text(title, limit: 512),
              let prompt = value["prompt"].string, ManagementValidation.text(prompt, limit: 8000),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              let enabled = value["enabled"].bool,
              let revision = value["revision"].integer, revision > 0,
              ScheduleRuleValidation.valid(value["spec"])
        else { throw RPCFailure.invalidResponse }
        if value["next_at"] != .null && ManagementValidation.timestamp(value["next_at"]) == nil { throw RPCFailure.invalidResponse }
        self.id = id; self.title = title; self.prompt = prompt; agentID = agent; workspaceID = workspace
        spec = value["spec"]; self.enabled = enabled; self.revision = revision
        nextAt = ManagementValidation.timestamp(value["next_at"]).map(Date.init(timeIntervalSince1970:))
    }
    public var revisionParams: [String: RPCValue] { ["schedule_id": .string(id), "expected_revision": .number(Double(revision))] }
    public var ruleDescription: String {
        switch spec["kind"].string {
        case "once": return "Una vez · UTC"
        case "interval": return "Cada \(spec["interval_seconds"].integer ?? 0) segundos"
        case "daily": return "Diaria · \(spec["local_time"].string ?? "") · \(spec["timezone"].string ?? "")"
        case "cron": return "Cron · \(spec["cron_expression"].string ?? "") · \(spec["timezone"].string ?? "")"
        case "heartbeat": return "Comprobación periódica · cada \(spec["interval_seconds"].integer ?? 0) segundos"
        default: return "Por evento · sin ejecución periódica"
        }
    }
}

public struct SchedulerSnapshot: Sendable {
    public let schedules: [ScheduleDescriptor]
    public let configurationHash: String
    public let running: Bool
    public let suspended: Bool
    public let workspaceID: String
    public let errorCode: String?
    public let rawSchedules: RPCValue
    public init(_ value: RPCValue) throws {
        schedules = try ManagementValidation.rows(value["schedules"], limit: 32, parse: ScheduleDescriptor.init)
        guard Set(schedules.map(\.id)).count == schedules.count,
              let hash = value["configuration_hash"].string, ManagementValidation.hash(hash),
              let running = value["runner_running"].bool,
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace)
        else { throw RPCFailure.invalidResponse }
        if value["suspended"] != .null && value["suspended"].bool == nil { throw RPCFailure.invalidResponse }
        configurationHash = hash; self.running = running; suspended = value["suspended"].bool ?? false; workspaceID = workspace
        errorCode = value["error"].string; rawSchedules = value["schedules"]
    }
}

public struct ScheduleOccurrenceSnapshot: Identifiable, Equatable, Sendable {
    public let id: String
    public let scheduleID: String
    public let status: String
    public let outcome: String
    public let scheduledAt: Date
    public let runID: String?
    public let sessionID: String?
    public let errorCode: String?
    public init(_ value: RPCValue) throws {
        guard let id = value["occurrence_id"].string, ManagementValidation.identifier(id),
              let scheduleID = value["schedule_id"].string, ManagementValidation.identifier(scheduleID),
              let status = value["status"].string, ["claimed", "running", "completed", "failed", "cancelled", "interrupted"].contains(status),
              let outcome = value["outcome"].string, ["pending", "known", "unknown"].contains(outcome),
              let at = ManagementValidation.timestamp(value["scheduled_at"]),
              (["claimed", "running"].contains(status)) == (outcome == "pending"),
              !(status == "completed" && outcome != "known") else { throw RPCFailure.invalidResponse }
        for key in ["run_id", "session_id"] {
            if value[key] != .null {
                guard let identity = value[key].string, ManagementValidation.identifier(identity) else { throw RPCFailure.invalidResponse }
            }
        }
        self.id = id; self.scheduleID = scheduleID; self.status = status; self.outcome = outcome; scheduledAt = Date(timeIntervalSince1970: at)
        runID = value["run_id"].string; sessionID = value["session_id"].string; errorCode = value["error_code"].string
    }
}

public struct ProcessSnapshot: Identifiable, Equatable, Sendable {
    public let id: String
    public let state: String
    public let running: Bool
    public let exitCode: Int?
    public let output: String
    public let nextCursor: Int
    public let hasMore: Bool
    public let outputBytes: Int
    public let retainedBytes: Int
    public let hash: String
    public let truncated: Bool
    public let backend: String
    public init(_ value: RPCValue) throws {
        guard let id = value["process_id"].string, ManagementValidation.identifier(id),
              let state = value["state"].string, ["running", "completed", "failed", "cancelled", "stopped", "timed_out", "output_limit", "stdin_unconfirmed", "scope_closed", "manager_closed"].contains(state),
              let running = value["running"].bool, (state == "running") == running,
              let output = value["output"].string, output.utf8.count <= 16384,
              let next = value["next_cursor"].integer, let cursor = value["cursor"].integer, cursor >= 0, next >= cursor,
              let retained = value["retained_bytes"].integer, next <= retained, retained <= 1_048_576,
              let bytes = value["output_bytes"].integer, bytes >= retained,
              let more = value["has_more"].bool, more == (next < retained),
              let hash = value["output_sha256"].string, ManagementValidation.hash(hash),
              let truncated = value["output_truncated"].bool,
              let backend = value["backend"].string, ["macos_seatbelt", "linux_bubblewrap"].contains(backend),
              value["effects_independently_verified"].bool == false,
              value["verification"].string == "process_state_only"
        else { throw RPCFailure.invalidResponse }
        if value["exit_code"] != .null && value["exit_code"].integer == nil { throw RPCFailure.invalidResponse }
        if running && value["exit_code"] != .null { throw RPCFailure.invalidResponse }
        self.id = id; self.state = state; self.running = running; exitCode = value["exit_code"].integer
        self.output = output; nextCursor = next; hasMore = more; outputBytes = bytes; retainedBytes = retained
        self.hash = hash; self.truncated = truncated; self.backend = backend
    }
}

public struct ProcessCatalogSnapshot: Sendable {
    public let available: Bool
    public let backend: String?
    public let processes: [ProcessSnapshot]
    public init(_ value: RPCValue) throws {
        guard let available = value["sandbox"]["available"].bool else { throw RPCFailure.invalidResponse }
        self.available = available
        processes = try ManagementValidation.rows(value["processes"], limit: 32, parse: ProcessSnapshot.init)
        guard Set(processes.map(\.id)).count == processes.count else { throw RPCFailure.invalidResponse }
        backend = value["sandbox"]["backend"].string
        if available {
            guard let backend, ["macos_seatbelt", "linux_bubblewrap"].contains(backend),
                  value["sandbox"]["network"].string == "denied",
                  value["sandbox"]["effects_independently_verified"].bool == false else { throw RPCFailure.invalidResponse }
        }
    }
}
