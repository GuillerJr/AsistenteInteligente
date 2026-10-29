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

    public static func groupChatID(_ value: String) -> Int? {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.range(of: "^-[1-9][0-9]{0,15}\\z", options: .regularExpression) != nil,
              let number = Int(text), number >= -maximumIdentity else { return nil }
        return number
    }

    static func routeIdentity(_ value: RPCValue, type: TelegramChatType? = nil) -> Int? {
        guard let number = integer(value, minimum: -maximumIdentity), number != 0,
              type == nil || (type == .privateChat ? number > 0 : number < 0) else { return nil }
        return number
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

public enum TelegramChatType: String, CaseIterable, Hashable, Sendable {
    case privateChat = "private", group, supergroup
    public var label: String {
        switch self { case .privateChat: return "Privado"; case .group: return "Grupo"; case .supergroup: return "Supergrupo" }
    }
}

public struct TelegramRoutePolicy: Equatable, Sendable {
    public let chatType: TelegramChatType
    public let allowedSenderIDs: [Int]
    public let messageThreadID: Int?
    public let allowAttachments: Bool

    public init(chatType: TelegramChatType, allowedSenderIDs: [Int] = [], messageThreadID: Int? = nil, allowAttachments: Bool = false) throws {
        guard allowedSenderIDs.count <= 32, Set(allowedSenderIDs).count == allowedSenderIDs.count,
              allowedSenderIDs.allSatisfy({ $0 > 0 && $0 <= TelegramValidation.maximumIdentity }),
              (chatType == .privateChat ? allowedSenderIDs.isEmpty : !allowedSenderIDs.isEmpty),
              messageThreadID == nil || (chatType == .supergroup && messageThreadID! > 0 && messageThreadID! <= 2_147_483_647)
        else { throw RPCFailure.configuration }
        self.chatType = chatType; self.allowedSenderIDs = allowedSenderIDs; self.messageThreadID = messageThreadID; self.allowAttachments = allowAttachments
    }

    init(_ value: RPCValue) throws {
        guard let kind = value["chat_type"].string.flatMap(TelegramChatType.init(rawValue:)),
              let rows = value["allowed_sender_ids"].array, let attachments = value["allow_attachments"].bool else { throw RPCFailure.invalidResponse }
        let senders = try rows.map { row -> Int in
            guard let number = TelegramValidation.integer(row, minimum: 1) else { throw RPCFailure.invalidResponse }; return number
        }
        let thread = value["message_thread_id"] == .null ? nil : TelegramValidation.integer(value["message_thread_id"], minimum: 1, maximum: 2_147_483_647)
        guard value["message_thread_id"] == .null || thread != nil else { throw RPCFailure.invalidResponse }
        do { try self.init(chatType: kind, allowedSenderIDs: senders, messageThreadID: thread, allowAttachments: attachments) }
        catch { throw RPCFailure.invalidResponse }
    }

    public var params: [String: RPCValue] {
        ["chat_type": .string(chatType.rawValue), "allowed_sender_ids": .array(allowedSenderIDs.map { .number(Double($0)) }),
         "message_thread_id": messageThreadID.map { .number(Double($0)) } ?? .null, "allow_attachments": .bool(allowAttachments)]
    }
}

public struct TelegramRouteSnapshot: Identifiable, Equatable, Sendable {
    public let chatID: Int
    public let agentID: String
    public let sessionID: String
    public let revision: Int
    public let enabled: Bool
    public let policy: TelegramRoutePolicy
    private let extended: Bool
    public var chatType: TelegramChatType { policy.chatType }
    public var id: Int { chatID }

    public init(_ value: RPCValue) throws {
        let base: Set<String> = ["chat_id", "agent_id", "session_id", "revision", "enabled"]
        let extra: Set<String> = ["chat_type", "allowed_sender_ids", "message_thread_id", "allow_attachments"]
        guard let object = value.object, Set(object.keys) == base || Set(object.keys) == base.union(extra) else { throw RPCFailure.invalidResponse }
        extended = Set(object.keys) != base
        policy = try (extended ? TelegramRoutePolicy(value) : TelegramRoutePolicy(chatType: .privateChat))
        guard let chat = TelegramValidation.routeIdentity(value["chat_id"], type: policy.chatType),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let session = value["session_id"].string, ManagementValidation.identifier(session),
              let revision = value["revision"].integer, revision > 0,
              let enabled = value["enabled"].bool else { throw RPCFailure.invalidResponse }
        chatID = chat; agentID = agent; sessionID = session; self.revision = revision; self.enabled = enabled
    }

    public var reviewValue: RPCValue {
        var row: [String: RPCValue] = ["chat_id": .number(Double(chatID)), "agent_id": .string(agentID), "session_id": .string(sessionID),
                 "revision": .number(Double(revision)), "enabled": .bool(enabled)]
        if extended { row.merge(policy.params) { _, new in new } }
        return .object(row)
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
        let required = try requiredRows.map { row -> Int in
            guard let number = TelegramValidation.routeIdentity(row) else { throw RPCFailure.invalidResponse }; return number
        }
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
    public let chatType: TelegramChatType?
    public let senderID: Int?
    public let messageThreadID: Int?
    public let attachmentCount: Int
    public var canCancel: Bool { status == "dispatching" && runID != nil && routeRevision != nil }
    public var id: String { "\(epoch):\(updateID)" }

    public init(_ value: RPCValue) throws {
        let base: Set<String> = ["update_id", "epoch", "chat_id", "message_id", "agent_id", "session_id",
                "route_revision", "status", "outcome", "run_id", "error_code", "outgoing_message_id", "reply_sha256", "reply_truncated"]
        let extra: Set<String> = ["chat_type", "sender_id", "message_thread_id", "attachment_count"]
        guard let object = value.object, Set(object.keys) == base || Set(object.keys) == base.union(extra),
              let update = TelegramValidation.integer(value["update_id"]), let epoch = TelegramValidation.integer(value["epoch"]),
              let status = value["status"].string,
              ["claimed", "dispatching", "send_pending", "completed", "ignored", "failed", "interrupted", "cancelled"].contains(status),
              let outcome = value["outcome"].string, ["pending", "known", "unknown"].contains(outcome),
              ["claimed", "dispatching", "send_pending"].contains(status) == (outcome == "pending"),
              status != "completed" || outcome == "known", let truncated = value["reply_truncated"].bool
        else { throw RPCFailure.invalidResponse }
        let chat: Int?, message: Int?, agent: String?, session: String?, revision: Int?
        if value["chat_id"] != .null {
            guard let kind = Set(object.keys) == base ? TelegramChatType.privateChat : value["chat_type"].string.flatMap(TelegramChatType.init(rawValue:)),
                  let chatValue = TelegramValidation.routeIdentity(value["chat_id"], type: kind),
                  let messageValue = TelegramValidation.integer(value["message_id"], minimum: 1, maximum: 2_147_483_647),
                  let agentValue = value["agent_id"].string, ManagementValidation.identifier(agentValue, agent: true),
                  let sessionValue = value["session_id"].string, ManagementValidation.identifier(sessionValue),
                  let revisionValue = value["route_revision"].integer, revisionValue > 0 else { throw RPCFailure.invalidResponse }
            chat = chatValue; message = messageValue; agent = agentValue; session = sessionValue; revision = revisionValue
            if Set(object.keys) != base {
                guard let sender = TelegramValidation.integer(value["sender_id"], minimum: 1),
                      let count = TelegramValidation.integer(value["attachment_count"], maximum: 1),
                      kind != .privateChat || sender == chatValue else { throw RPCFailure.invalidResponse }
                let thread = value["message_thread_id"] == .null ? nil : TelegramValidation.integer(value["message_thread_id"], minimum: 1, maximum: 2_147_483_647)
                guard value["message_thread_id"] == .null || (kind == .supergroup && thread != nil) else { throw RPCFailure.invalidResponse }
                chatType = kind; senderID = sender; messageThreadID = thread; attachmentCount = count
            } else { chatType = .privateChat; senderID = chatValue; messageThreadID = nil; attachmentCount = 0 }
        } else {
            guard Set(object.keys) == base, ["message_id", "agent_id", "session_id", "route_revision"].allSatisfy({ value[$0] == .null }) else { throw RPCFailure.invalidResponse }
            chat = nil; message = nil; agent = nil; session = nil; revision = nil
            chatType = nil; senderID = nil; messageThreadID = nil; attachmentCount = 0
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
