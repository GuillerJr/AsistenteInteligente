import Foundation

public struct TelegramGroupDiscoveryReview: Identifiable, Equatable, Sendable {
    public let ownerChatID: Int
    public let agentID: String
    public let agentRevision: Int
    public let botID: Int
    public let username: String
    public let workspaceID: String
    public let profileID: String
    public let configurationHash: String
    public let allowAttachments: Bool
    public let reviewHash: String
    public var id: String { reviewHash }
    public let reviewValue: RPCValue

    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["owner_chat_id", "agent_id", "agent_revision", "bot_id", "username", "workspace_id", "profile_id", "configuration_hash", "allow_attachments", "max_seconds", "consumes_ignored_updates", "requires_exclusive_bot", "telegram_retains_start_message", "review_hash"])
        guard let owner = TelegramValidation.integer(value["owner_chat_id"], minimum: 1),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let revision = TelegramValidation.integer(value["agent_revision"], minimum: 1),
              let bot = TelegramValidation.integer(value["bot_id"], minimum: 1), owner != bot,
              let username = value["username"].string, username.range(of: "^[A-Za-z0-9_]{1,64}\\z", options: .regularExpression) != nil,
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              let profile = value["profile_id"].string, MaintenanceValidation.safeID(profile),
              let configuration = value["configuration_hash"].string, ManagementValidation.hash(configuration),
              let attachments = value["allow_attachments"].bool,
              let hash = value["review_hash"].string, ManagementValidation.hash(hash),
              value["max_seconds"].integer == 180, value["consumes_ignored_updates"] == .bool(true),
              value["requires_exclusive_bot"] == .bool(true), value["telegram_retains_start_message"] == .bool(true)
        else { throw RPCFailure.invalidResponse }
        ownerChatID = owner; agentID = agent; agentRevision = revision; botID = bot
        self.username = username; workspaceID = workspace; profileID = profile
        configurationHash = configuration; allowAttachments = attachments; reviewHash = hash; reviewValue = value
    }
}

/// Only reviewed metadata. Neither command nor nonce is a status property.
public struct TelegramGroupDiscoveryStatus: Equatable, Sendable {
    public let state: String
    public let challengeID: String?
    public let claimID: String?
    public let ownerChatID: Int?
    public let chatID: Int?
    public let chatType: TelegramChatType?
    public let messageThreadID: Int?
    public let senderID: Int?
    public let agentID: String?
    public let agentRevision: Int?
    public let configurationHash: String?
    public let allowAttachments: Bool
    public let errorCode: String?
    public let expiresAt: Date?
    public let claimReviewValue: RPCValue
    public var active: Bool { ["starting", "listening", "claimed"].contains(state) }

    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["state", "challenge_id", "claim_id", "owner_chat_id", "chat_id", "chat_type", "message_thread_id", "sender_id", "agent_id", "agent_revision", "configuration_hash", "allow_attachments", "error_code", "expires_in_seconds"])
        guard let state = value["state"].string, ["idle", "starting", "listening", "claimed", "confirmed", "expired", "cancelled", "failed"].contains(state),
              let attachments = value["allow_attachments"].bool else { throw RPCFailure.invalidResponse }
        self.state = state; allowAttachments = attachments; claimReviewValue = value
        challengeID = try TelegramValidation.optionalID(value["challenge_id"], session: true)
        claimID = try TelegramValidation.optionalID(value["claim_id"], session: true)
        errorCode = try TelegramValidation.errorCode(value["error_code"])
        if state == "idle" {
            guard ["challenge_id", "claim_id", "owner_chat_id", "chat_id", "chat_type", "message_thread_id", "sender_id", "agent_id", "agent_revision", "configuration_hash", "error_code", "expires_in_seconds"].allSatisfy({ value[$0] == .null }), !attachments else { throw RPCFailure.invalidResponse }
            ownerChatID = nil; chatID = nil; chatType = nil; messageThreadID = nil; senderID = nil
            agentID = nil; agentRevision = nil; configurationHash = nil; expiresAt = nil
            return
        }
        guard let owner = TelegramValidation.integer(value["owner_chat_id"], minimum: 1),
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let revision = TelegramValidation.integer(value["agent_revision"], minimum: 1),
              let configuration = value["configuration_hash"].string, ManagementValidation.hash(configuration),
              let ttl = TelegramValidation.integer(value["expires_in_seconds"], maximum: 180), challengeID != nil,
              !["starting", "listening", "claimed"].contains(state) || ttl > 0,
              (state == "claimed") == (claimID != nil), claimID == nil || claimID != challengeID
        else { throw RPCFailure.invalidResponse }
        ownerChatID = owner; agentID = agent; agentRevision = revision; configurationHash = configuration
        expiresAt = now.addingTimeInterval(Double(ttl))
        if ["claimed", "confirmed"].contains(state) {
            guard let kind = value["chat_type"].string.flatMap(TelegramChatType.init(rawValue:)), kind != .privateChat,
                  let chat = TelegramValidation.routeIdentity(value["chat_id"], type: kind),
                  TelegramValidation.integer(value["sender_id"], minimum: 1) == owner else { throw RPCFailure.invalidResponse }
            let thread = value["message_thread_id"] == .null ? nil : TelegramValidation.integer(value["message_thread_id"], minimum: 1, maximum: 2_147_483_647)
            guard value["message_thread_id"] == .null || (kind == .supergroup && thread != nil) else { throw RPCFailure.invalidResponse }
            chatID = chat; chatType = kind; messageThreadID = thread; senderID = owner
        } else {
            guard ["chat_id", "chat_type", "message_thread_id", "sender_id"].allSatisfy({ value[$0] == .null }) else { throw RPCFailure.invalidResponse }
            chatID = nil; chatType = nil; messageThreadID = nil; senderID = nil
        }
    }

    public func matches(_ review: TelegramGroupDiscoveryReview) -> Bool {
        ownerChatID == review.ownerChatID && agentID == review.agentID && agentRevision == review.agentRevision && configurationHash == review.configurationHash && allowAttachments == review.allowAttachments
    }
}
