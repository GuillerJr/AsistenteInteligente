import Foundation

public struct TelegramGroupPairingReview: Identifiable, Equatable, Sendable {
    public let base: TelegramPairingReview
    public let chatID: Int
    public let routeRevision: Int
    public let policy: TelegramRoutePolicy
    public var id: String { base.id }
    public init(_ value: RPCValue) throws {
        let extra: Set<String> = ["chat_id", "chat_type", "allowed_sender_ids", "message_thread_id", "route_revision", "allow_attachments"]
        guard var object = value.object, extra.isSubset(of: Set(object.keys)) else { throw RPCFailure.invalidResponse }
        policy = try TelegramRoutePolicy(value)
        guard policy.chatType != .privateChat, let chat = TelegramValidation.routeIdentity(value["chat_id"], type: policy.chatType),
              let revision = value["route_revision"].integer, revision > 0 else { throw RPCFailure.invalidResponse }
        chatID = chat; routeRevision = revision
        for key in extra { object.removeValue(forKey: key) }
        base = try TelegramPairingReview(.object(object))
    }
    public var reviewValue: RPCValue {
        var object = base.reviewValue.object!; object.merge(policy.params) { _, new in new }
        object["chat_id"] = .number(Double(chatID)); object["route_revision"] = .number(Double(routeRevision))
        return .object(object)
    }
}

/// Finite metadata only. The proof command/nonce is never a status property.
public struct TelegramGroupPairingStatus: Equatable, Sendable {
    public let base: TelegramPairingStatus
    public let targetChatID: Int?
    public let chatType: TelegramChatType?
    public let allowedSenderIDs: [Int]
    public let messageThreadID: Int?
    public let routeRevision: Int?
    public let senderID: Int?
    public var active: Bool { base.active }
    public var state: String { base.state }
    public var chatID: Int? { base.chatID == nil ? nil : targetChatID }

    public init(_ value: RPCValue) throws {
        let extra: Set<String> = ["target_chat_id", "chat_type", "allowed_sender_ids", "message_thread_id", "route_revision", "sender_id"]
        guard var object = value.object, extra.isSubset(of: Set(object.keys)), let senders = value["allowed_sender_ids"].array else { throw RPCFailure.invalidResponse }
        if value["state"].string == "idle" {
            guard senders.isEmpty, extra.subtracting(["allowed_sender_ids"]).allSatisfy({ value[$0] == .null }) else { throw RPCFailure.invalidResponse }
            targetChatID = nil; chatType = nil; allowedSenderIDs = []; messageThreadID = nil; routeRevision = nil; senderID = nil
        } else {
            var policyObject = object; policyObject["allow_attachments"] = .bool(false)
            let policy = try TelegramRoutePolicy(.object(policyObject))
            guard policy.chatType != .privateChat, let chat = TelegramValidation.routeIdentity(value["target_chat_id"], type: policy.chatType),
                  let revision = value["route_revision"].integer, revision > 0 else { throw RPCFailure.invalidResponse }
            if value["chat_id"] != .null {
                guard value["chat_id"].integer == chat, let sender = TelegramValidation.integer(value["sender_id"], minimum: 1), policy.allowedSenderIDs.contains(sender) else { throw RPCFailure.invalidResponse }
                senderID = sender
                // Reuse private pairing's common state invariants while retaining
                // the signed destination separately; this value never leaves parsing.
                object["chat_id"] = .number(Double(-chat))
            } else {
                guard value["sender_id"] == .null else { throw RPCFailure.invalidResponse }; senderID = nil
            }
            targetChatID = chat; chatType = policy.chatType; allowedSenderIDs = policy.allowedSenderIDs
            messageThreadID = policy.messageThreadID; routeRevision = revision
        }
        for key in extra { object.removeValue(forKey: key) }
        base = try TelegramPairingStatus(.object(object))
    }

    public var claimReviewValue: RPCValue {
        var value = base.claimReviewValue.object!
        value["chat_id"] = chatID.map { .number(Double($0)) } ?? .null
        value["target_chat_id"] = targetChatID.map { .number(Double($0)) } ?? .null
        value["chat_type"] = chatType.map { .string($0.rawValue) } ?? .null
        value["allowed_sender_ids"] = .array(allowedSenderIDs.map { .number(Double($0)) })
        value["sender_id"] = senderID.map { .number(Double($0)) } ?? .null
        value["message_thread_id"] = messageThreadID.map { .number(Double($0)) } ?? .null
        value["route_revision"] = routeRevision.map { .number(Double($0)) } ?? .null
        return .object(value)
    }
}

@MainActor public protocol TelegramNativeCommandCopier: AnyObject {
    func copyTelegramCommand(_ command: String) -> Bool
}

public enum TelegramGroupCommandFailure: Error, Equatable, LocalizedError {
    case alreadyAttempted, invalidCommand, copyFailed, cleanupUnconfirmed
    public var errorDescription: String? {
        switch self {
        case .alreadyAttempted: return "El código ya tuvo un intento de copia. Cancela este enlace e inicia otro si lo necesitas."
        case .invalidCommand: return "No se pudo validar el código del grupo. El intento se canceló."
        case .copyFailed: return "No se confirmó la copia al portapapeles. El intento se canceló; inicia otro para obtener un código nuevo."
        case .cleanupUnconfirmed: return "No se confirmó la copia ni el cierre del enlace. Actualiza y solicita su cancelación explícita."
        }
    }
}
