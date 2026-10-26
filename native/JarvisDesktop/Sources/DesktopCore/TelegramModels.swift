import Foundation

public enum TelegramValidation {
    public static let maximumIdentity = 4_503_599_627_370_495

    public static func token(_ value: String) -> Bool {
        value.utf8.count <= 148 && value.range(of: "^[1-9][0-9]{0,18}:[A-Za-z0-9_-]{20,128}\\z", options: .regularExpression) != nil
    }

    public static func chatID(_ value: String) -> Int? {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.range(of: "^[1-9][0-9]{0,15}\\z", options: .regularExpression) != nil,
              let value = Int(text), value <= maximumIdentity else { return nil }
        return value
    }

    static func integer(_ value: RPCValue, minimum: Int = 0, maximum: Int = maximumIdentity) -> Int? {
        guard let result = value.integer, result >= minimum, result <= maximum else { return nil }
        return result
    }

    static func optionalID(_ value: RPCValue, session: Bool = false) throws -> String? {
        if value == .null { return nil }
        guard let result = value.string, session ? ManagementValidation.identifier(result) : ManagementValidation.hash(result) else {
            throw RPCFailure.invalidResponse
        }
        return result
    }

    static func errorCode(_ value: RPCValue) throws -> String? {
        if value == .null { return nil }
        guard let code = value.string,
              code.range(of: "^[a-z][a-z0-9_]{0,127}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }
        return code
    }
}

public struct TelegramRouteSnapshot: Identifiable, Equatable, Sendable {
    public let chatID: Int
    public let agentID: String
    public let sessionID: String
    public let revision: Int
    public let enabled: Bool
    public var id: Int { chatID }

    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys) == ["chat_id", "agent_id", "session_id", "revision", "enabled"],
              let chat = TelegramValidation.integer(value["chat_id"], minimum: 1),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let session = value["session_id"].string, ManagementValidation.identifier(session),
              let revision = value["revision"].integer, revision > 0,
              let enabled = value["enabled"].bool else { throw RPCFailure.invalidResponse }
        chatID = chat; agentID = agent; sessionID = session; self.revision = revision; self.enabled = enabled
    }

    public var reviewValue: RPCValue {
        .object(["chat_id": .number(Double(chatID)), "agent_id": .string(agentID), "session_id": .string(sessionID),
                 "revision": .number(Double(revision)), "enabled": .bool(enabled)])
    }
}

public struct TelegramSnapshot: Equatable, Sendable {
    public let connected: Bool
    public let running: Bool
    public let botID: Int?
    public let username: String?
    public let routes: [TelegramRouteSnapshot]
    public let configurationHash: String
    public let workspaceID: String
    public let pinnedWorkspaceID: String?
    public let errorCode: String?
    public let restoredPairingRequiredChatIDs: [Int]

    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys) == ["connected", "running", "bot_id", "username", "routes",
                "configuration_hash", "workspace_id", "pinned_workspace_id", "error", "restored_pairing_required_chat_ids"],
              let connected = value["connected"].bool, let running = value["running"].bool,
              let hash = value["configuration_hash"].string, ManagementValidation.hash(hash),
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace)
        else { throw RPCFailure.invalidResponse }
        let routes = try ManagementValidation.rows(value["routes"], limit: 32, parse: TelegramRouteSnapshot.init)
        guard Set(routes.map(\.chatID)).count == routes.count else { throw RPCFailure.invalidResponse }
        guard let requiredRows = value["restored_pairing_required_chat_ids"].array, requiredRows.count <= 32 else { throw RPCFailure.invalidResponse }
        let required = try requiredRows.map { try ChannelReconciliationValidation.identity($0) }
        // The reader may still own an in-flight poll when a route loses its
        // fresh proof. Keep that ownership visible so Stop remains available.
        guard Set(required).count == required.count, required.allSatisfy({ id in routes.contains { $0.chatID == id && $0.enabled } }) else { throw RPCFailure.invalidResponse }
        restoredPairingRequiredChatIDs = required
        let pinned = try TelegramValidation.optionalID(value["pinned_workspace_id"])
        let bot: Int?
        let username: String?
        if connected {
            guard let identity = TelegramValidation.integer(value["bot_id"], minimum: 1) else { throw RPCFailure.invalidResponse }
            bot = identity
            if value["username"] == .null { username = nil }
            else {
                guard let name = value["username"].string,
                      name.range(of: "^[A-Za-z0-9_]{1,64}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }
                username = name
            }
        } else {
            guard value["bot_id"] == .null, value["username"] == .null else { throw RPCFailure.invalidResponse }
            bot = nil; username = nil
        }
        guard !running || (connected && pinned == workspace) else { throw RPCFailure.invalidResponse }
        self.connected = connected; self.running = running; botID = bot; self.username = username; self.routes = routes
        configurationHash = hash; workspaceID = workspace; pinnedWorkspaceID = pinned
        errorCode = try TelegramValidation.errorCode(value["error"])
    }

    /// A finite allowlist assembled from validated fields. Never includes a
    /// credential, an inbound/outbound message, or an arbitrary server payload.
    public var reviewValue: RPCValue {
        .object(["bot_id": botID.map { .number(Double($0)) } ?? .null,
                 "username": username.map(RPCValue.string) ?? .null,
                 "routes": .array(routes.map(\.reviewValue)), "workspace_id": .string(workspaceID),
                 "configuration_hash": .string(configurationHash), "restored_pairing_required_chat_ids": .array(restoredPairingRequiredChatIDs.map { .number(Double($0)) })])
    }
}

public struct TelegramEventSnapshot: Identifiable, Equatable, Sendable {
    public let updateID: Int
    public let epoch: Int
    public let chatID: Int?
    public let messageID: Int?
    public let agentID: String?
    public let sessionID: String?
    public let routeRevision: Int?
    public let status: String
    public let outcome: String
    public let runID: String?
    public let errorCode: String?
    public let outgoingMessageID: Int?
    public let replyHash: String?
    public let replyTruncated: Bool
    public var id: String { "\(epoch):\(updateID)" }

    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys) == ["update_id", "epoch", "chat_id", "message_id", "agent_id", "session_id",
                "route_revision", "status", "outcome", "run_id", "error_code", "outgoing_message_id", "reply_sha256", "reply_truncated"],
              let update = TelegramValidation.integer(value["update_id"]), let epoch = TelegramValidation.integer(value["epoch"]),
              let status = value["status"].string,
              ["claimed", "dispatching", "send_pending", "completed", "ignored", "failed", "interrupted", "cancelled"].contains(status),
              let outcome = value["outcome"].string, ["pending", "known", "unknown"].contains(outcome),
              ["claimed", "dispatching", "send_pending"].contains(status) == (outcome == "pending"),
              status != "completed" || outcome == "known", let truncated = value["reply_truncated"].bool
        else { throw RPCFailure.invalidResponse }
        let chat: Int?, message: Int?, agent: String?, session: String?, revision: Int?
        if value["chat_id"] != .null {
            guard let chatValue = TelegramValidation.integer(value["chat_id"], minimum: 1),
                  let messageValue = TelegramValidation.integer(value["message_id"], minimum: 1, maximum: 2_147_483_647),
                  let agentValue = value["agent_id"].string, ManagementValidation.identifier(agentValue, agent: true),
                  let sessionValue = value["session_id"].string, ManagementValidation.identifier(sessionValue),
                  let revisionValue = value["route_revision"].integer, revisionValue > 0 else { throw RPCFailure.invalidResponse }
            chat = chatValue; message = messageValue; agent = agentValue; session = sessionValue; revision = revisionValue
        } else {
            guard ["message_id", "agent_id", "session_id", "route_revision"].allSatisfy({ value[$0] == .null }) else { throw RPCFailure.invalidResponse }
            chat = nil; message = nil; agent = nil; session = nil; revision = nil
        }
        let outgoing: Int?
        if value["outgoing_message_id"] == .null { outgoing = nil }
        else {
            guard let number = TelegramValidation.integer(value["outgoing_message_id"], minimum: 1, maximum: 2_147_483_647) else { throw RPCFailure.invalidResponse }
            outgoing = number
        }
        updateID = update; self.epoch = epoch; chatID = chat; messageID = message; agentID = agent; sessionID = session
        routeRevision = revision; self.status = status; self.outcome = outcome; replyTruncated = truncated
        runID = try TelegramValidation.optionalID(value["run_id"], session: true)
        replyHash = try TelegramValidation.optionalID(value["reply_sha256"])
        errorCode = try TelegramValidation.errorCode(value["error_code"])
        outgoingMessageID = outgoing
    }
}

public struct TelegramHistorySnapshot: Equatable, Sendable {
    public let events: [TelegramEventSnapshot]
    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys) == ["events", "limit", "capacity"],
              value["limit"].integer == 50, value["capacity"].integer == 2048 else { throw RPCFailure.invalidResponse }
        events = try ManagementValidation.rows(value["events"], limit: 50, parse: TelegramEventSnapshot.init)
        guard Set(events.map(\.id)).count == events.count else { throw RPCFailure.invalidResponse }
    }
}
