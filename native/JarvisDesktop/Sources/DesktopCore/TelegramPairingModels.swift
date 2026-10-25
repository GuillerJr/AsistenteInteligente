import Foundation

public struct TelegramPairingReview: Identifiable, Equatable, Sendable {
    public let agentID: String
    public let agentRevision: Int
    public let botID: Int
    public let username: String
    public let workspaceID: String
    public let reviewHash: String
    public var id: String { reviewHash }

    public init(_ value: RPCValue) throws {
        guard let object = value.object,
              Set(object.keys) == ["agent_id", "agent_revision", "bot_id", "username", "workspace_id", "max_seconds",
                  "consumes_ignored_updates", "requires_exclusive_bot", "telegram_retains_start_message", "review_hash"],
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let revision = value["agent_revision"].integer, revision > 0,
              let bot = TelegramValidation.integer(value["bot_id"], minimum: 1),
              let name = value["username"].string,
              name.range(of: "^[A-Za-z0-9_]{1,64}\\z", options: .regularExpression) != nil,
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              let hash = value["review_hash"].string, ManagementValidation.hash(hash),
              value["max_seconds"].integer == 180, value["consumes_ignored_updates"].bool == true,
              value["requires_exclusive_bot"].bool == true, value["telegram_retains_start_message"].bool == true
        else { throw RPCFailure.invalidResponse }
        agentID = agent; agentRevision = revision; botID = bot; username = name; workspaceID = workspace; reviewHash = hash
    }

    public var reviewValue: RPCValue {
        .object(["agent_id": .string(agentID), "agent_revision": .number(Double(agentRevision)), "bot_id": .number(Double(botID)),
                 "username": .string(username), "workspace_id": .string(workspaceID), "review_hash": .string(reviewHash),
                 "max_seconds": .number(180), "consumes_ignored_updates": .bool(true), "requires_exclusive_bot": .bool(true),
                 "telegram_retains_start_message": .bool(true)])
    }
}

public struct TelegramPairingStatus: Equatable, Sendable {
    public let state: String
    public let challengeID: String?
    public let claimID: String?
    public let chatID: Int?
    public let agentID: String?
    public let agentRevision: Int?
    public let botID: Int?
    public let username: String?
    public let expiresAt: Date?
    public let errorCode: String?
    public var active: Bool { ["starting", "listening", "claimed"].contains(state) }

    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys) == ["state", "challenge_id", "claim_id", "chat_id", "agent_id",
                  "agent_revision", "bot_id", "username", "expires_at", "error"],
              let state = value["state"].string,
              ["idle", "starting", "listening", "claimed", "confirmed", "expired", "cancelled", "failed"].contains(state)
        else { throw RPCFailure.invalidResponse }
        let challenge = try TelegramValidation.optionalID(value["challenge_id"], session: true)
        let claim = try TelegramValidation.optionalID(value["claim_id"], session: true)
        let chat: Int?
        if value["chat_id"] == .null { chat = nil }
        else {
            guard let identity = TelegramValidation.integer(value["chat_id"], minimum: 1) else { throw RPCFailure.invalidResponse }
            chat = identity
        }
        let agent: String?, revision: Int?
        if value["agent_id"] == .null {
            guard value["agent_revision"] == .null else { throw RPCFailure.invalidResponse }
            agent = nil; revision = nil
        } else {
            guard let identity = value["agent_id"].string, ManagementValidation.identifier(identity, agent: true),
                  let version = value["agent_revision"].integer, version > 0 else { throw RPCFailure.invalidResponse }
            agent = identity; revision = version
        }
        let bot: Int?, username: String?
        if value["bot_id"] == .null {
            guard value["username"] == .null else { throw RPCFailure.invalidResponse }
            bot = nil; username = nil
        } else {
            guard let identity = TelegramValidation.integer(value["bot_id"], minimum: 1) else { throw RPCFailure.invalidResponse }
            bot = identity
            if value["username"] == .null { username = nil }
            else {
                guard let name = value["username"].string,
                      name.range(of: "^[A-Za-z0-9_]{1,64}\\z", options: .regularExpression) != nil else { throw RPCFailure.invalidResponse }
                username = name
            }
        }
        let expires: Date?
        if value["expires_at"] == .null { expires = nil }
        else {
            guard let timestamp = ManagementValidation.timestamp(value["expires_at"]) else { throw RPCFailure.invalidResponse }
            expires = Date(timeIntervalSince1970: timestamp)
        }
        guard (state == "claimed") == (claim != nil && chat != nil), (claim == nil) == (chat == nil),
              state != "claimed" || claim != challenge,
              !["starting", "listening", "claimed"].contains(state) ||
                (challenge != nil && agent != nil && revision != nil && bot != nil && username != nil && expires != nil),
              state != "idle" || (challenge == nil && agent == nil && bot == nil && expires == nil)
        else { throw RPCFailure.invalidResponse }
        self.state = state; challengeID = challenge; claimID = claim; chatID = chat; agentID = agent; agentRevision = revision
        botID = bot; self.username = username; expiresAt = expires; errorCode = try TelegramValidation.errorCode(value["error"])
    }

    public var claimReviewValue: RPCValue {
        .object(["claim_id": claimID.map(RPCValue.string) ?? .null, "chat_id": chatID.map { .number(Double($0)) } ?? .null,
                 "agent_id": agentID.map(RPCValue.string) ?? .null,
                 "expected_profile_revision": agentRevision.map { .number(Double($0)) } ?? .null,
                 "bot_id": botID.map { .number(Double($0)) } ?? .null,
                 "username": username.map(RPCValue.string) ?? .null])
    }
}

public enum TelegramPairingOpenFailure: Error, LocalizedError, Equatable {
    case unavailable, invalidLink, alreadyAttempted, failed, cleanupUnconfirmed
    public var errorDescription: String? {
        switch self {
        case .unavailable: return "No se encontró una app Telegram compatible instalada. El intento de enlace se canceló; puedes instalar Telegram e iniciar otro."
        case .invalidLink: return "No se pudo validar el enlace privado de Telegram. El intento se canceló."
        case .alreadyAttempted: return "Este enlace ya tuvo un intento de apertura. Cancélalo e inicia otro para obtener uno nuevo."
        case .failed: return "Telegram no confirmó la apertura. El intento de enlace se canceló; inicia otro cuando la app esté disponible."
        case .cleanupUnconfirmed: return "No se confirmó la apertura ni el cierre del intento de enlace. Actualiza su estado y solicita la cancelación de forma explícita."
        }
    }
}

@MainActor public protocol TelegramNativeURLOpener: AnyObject {
    func canOpenTelegram() -> Bool
    func openTelegram(_ sensitiveURL: URL) async -> Bool
}
