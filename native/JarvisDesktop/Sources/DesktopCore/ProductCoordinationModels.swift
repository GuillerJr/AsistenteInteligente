import Foundation

public enum ProductCoordinationValidation {
    public static func clientID(_ value: String) -> Bool { ManagementValidation.hash(value) }
    public static func cost(_ value: String) -> Bool {
        value.utf8.count <= 40 && value.range(
            of: "^(?:0|[1-9][0-9]{0,17})(?:\\.[0-9]{1,12})?\\z",
            options: .regularExpression) != nil
    }
    static func id(_ value: RPCValue, client: Bool = false) throws -> String {
        guard let value = value.string,
              client ? clientID(value) : ManagementValidation.identifier(value) else {
            throw RPCFailure.invalidResponse
        }
        return value
    }
    static func revision(_ value: RPCValue, zero: Bool = false) throws -> Int {
        guard let value = value.integer, value >= (zero ? 0 : 1) else {
            throw RPCFailure.invalidResponse
        }
        return value
    }
    static func amount(_ value: RPCValue) throws -> String? {
        if value == .null { return nil }
        guard let value = value.string, cost(value) else { throw RPCFailure.invalidResponse }
        return value
    }
    static func tokens(_ value: RPCValue) throws -> Int? {
        if value == .null { return nil }
        guard let value = value.integer, value >= 0 else { throw RPCFailure.invalidResponse }
        return value
    }
    static func scope(_ value: RPCValue) throws -> SessionScope {
        let id = try id(value["session_id"])
        guard let temporary = value["temporary"].bool else { throw RPCFailure.invalidResponse }
        return SessionScope(sessionID: id, temporary: temporary)
    }
    static func date(_ value: RPCValue) throws -> Date {
        // Nanoseconds exceed JSON's exact integer range. Dates are presentation
        // only; CAS revisions and identities never depend on a converted date.
        guard case .number(let number) = value, number.isFinite,
              number >= 0, number < 253_402_214_400_000_000_000 else {
            throw RPCFailure.invalidResponse
        }
        return Date(timeIntervalSince1970: number / 1_000_000_000)
    }
}

public struct ProductWorkspaceDescriptor: Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let path: String
    public let identity: [Int]
    public let revision: Int
    public let temporary: Bool
    public init(_ value: RPCValue) throws {
        id = try ProductCoordinationValidation.id(value["workspace_id"])
        name = try ProductInteractionValidation.text(value["name"], limit: 128)
        path = try ProductInteractionValidation.text(value["path"], limit: 4096)
        guard path.hasPrefix("/"), !path.split(separator: "/").contains(".."),
              let parts = value["identity"].array, parts.count == 2,
              let device = parts[0].integer, device >= 0,
              let inode = parts[1].integer, inode >= 0,
              let temporary = value["temporary"].bool else { throw RPCFailure.invalidResponse }
        identity = [device, inode]; self.temporary = temporary
        revision = try ProductCoordinationValidation.revision(value["revision"])
    }
}

public struct ProductSessionMember: Identifiable, Equatable, Sendable {
    public let id: String
    public let role: String
    public init(_ value: RPCValue) throws {
        id = try ProductCoordinationValidation.id(value["client_id"], client: true)
        guard let role = value["role"].string, ["read", "collaborate"].contains(role) else {
            throw RPCFailure.invalidResponse
        }
        self.role = role
    }
}

public struct ProductSessionBinding: Identifiable, Equatable, Sendable {
    public let scope: SessionScope
    public var id: String { scope.sessionID }
    public let workspaceID: String
    public let ownerID: String
    public let group: String?
    public let title: String?
    public let pinned: Bool
    public let parentID: String?
    public let depth: Int
    public let revision: Int
    public let members: [ProductSessionMember]
    public let securityGeneration: Int
    public var displayName: String { title ?? group ?? "Conversación \(id.prefix(8))" }
    public init(_ value: RPCValue) throws {
        scope = try ProductCoordinationValidation.scope(value)
        workspaceID = try ProductCoordinationValidation.id(value["workspace_id"])
        ownerID = try ProductCoordinationValidation.id(value["owner_id"], client: true)
        group = value["group"] == .null ? nil
            : try ProductInteractionValidation.text(value["group"], limit: 128)
        title = value["title"] == .null ? nil
            : try ProductInteractionValidation.text(value["title"], limit: 512)
        guard let pinned = value["pinned"].bool,
              let depth = value["depth"].integer, (0...10000).contains(depth),
              value["permissions_inherited"].bool == false else { throw RPCFailure.invalidResponse }
        self.pinned = pinned; self.depth = depth
        parentID = value["parent_session_id"] == .null ? nil
            : try ProductCoordinationValidation.id(value["parent_session_id"])
        guard parentID != scope.sessionID, (parentID == nil) == (depth == 0) else {
            throw RPCFailure.invalidResponse
        }
        revision = try ProductCoordinationValidation.revision(value["revision"])
        securityGeneration = value["security_generation"] == .null ? revision
            : try ProductCoordinationValidation.revision(value["security_generation"])
        members = try ManagementValidation.rows(value["members"], limit: 32,
                                                 parse: ProductSessionMember.init)
        guard Set(members.map(\.id)).count == members.count,
              !members.contains(where: { $0.id == ownerID }) else { throw RPCFailure.invalidResponse }
    }
    public func mayWrite(clientID: String) -> Bool {
        clientID == ownerID || members.contains { $0.id == clientID && $0.role == "collaborate" }
    }
}

public struct ProductAgentMessage: Identifiable, Equatable, Sendable {
    public let id: String
    public let requestID: String
    public let sourceID: String
    public let targetID: String
    public let senderID: String
    public let recipientID: String
    public let sourceAgentID: String
    public let targetAgentID: String
    public let correlationID: String
    public let parentID: String?
    public let body: String
    public let status: String
    public let revision: Int
    public let temporary: Bool
    public let createdAt: Date
    public let expiresAt: Date
    public var maySettle: Bool { status == "pending" && expiresAt > Date() }
    public init(_ value: RPCValue) throws {
        id = try ProductCoordinationValidation.id(value["message_id"])
        requestID = try ProductCoordinationValidation.id(value["request_id"])
        sourceID = try ProductCoordinationValidation.id(value["source_session_id"])
        targetID = try ProductCoordinationValidation.id(value["target_session_id"])
        senderID = try ProductCoordinationValidation.id(value["sender_id"], client: true)
        recipientID = try ProductCoordinationValidation.id(value["recipient_id"], client: true)
        sourceAgentID = try ProductInteractionValidation.identity(value["source_agent_id"])
        targetAgentID = try ProductInteractionValidation.identity(value["target_agent_id"])
        correlationID = try ProductCoordinationValidation.id(value["correlation_id"])
        parentID = value["parent_message_id"] == .null ? nil
            : try ProductCoordinationValidation.id(value["parent_message_id"])
        body = try ProductInteractionValidation.text(value["body"], limit: 8000)
        guard let hash = value["body_sha256"].string,
              hash == ManagementValidation.digest(body), let temporary = value["temporary"].bool,
              let status = value["status"].string,
              ["pending", "acknowledged", "rejected", "revoked", "expired"].contains(status),
              value["transport"].string == "local", value["grants_included"].bool == false,
              value["permits_replay"].bool == false, value["effect_verified"].bool == false else {
            throw RPCFailure.invalidResponse
        }
        self.temporary = temporary; self.status = status
        revision = try ProductCoordinationValidation.revision(value["revision"])
        createdAt = try ProductCoordinationValidation.date(value["created_ns"])
        expiresAt = try ProductCoordinationValidation.date(value["expires_ns"])
        guard expiresAt > createdAt else { throw RPCFailure.invalidResponse }
    }
}

public struct ProductBudgetSnapshot: Equatable, Sendable {
    public let scope: SessionScope
    public let tokenLimit: Int?
    public let costLimit: String?
    public let committedTokens: Int?
    public let committedCost: String?
    public let actualTokens: Int?
    public let actualCost: String?
    public let revision: Int
    public let usageKnown: Bool
    public let costKnown: Bool
    public let overTokenLimit: Bool
    public let overCostLimit: Bool
    public init(_ value: RPCValue) throws {
        scope = try ProductCoordinationValidation.scope(value)
        tokenLimit = try ProductCoordinationValidation.tokens(value["token_limit"])
        committedTokens = try ProductCoordinationValidation.tokens(value["committed_tokens"])
        actualTokens = try ProductCoordinationValidation.tokens(value["actual_tokens"])
        costLimit = try ProductCoordinationValidation.amount(value["cost_limit"])
        committedCost = try ProductCoordinationValidation.amount(value["committed_cost"])
        actualCost = try ProductCoordinationValidation.amount(value["actual_cost"])
        revision = try ProductCoordinationValidation.revision(value["revision"])
        guard value["currency"].string == "USD", let usageKnown = value["usage_known"].bool,
              let costKnown = value["cost_known"].bool,
              let overToken = value["over_token_limit"].bool,
              let overCost = value["over_cost_limit"].bool,
              usageKnown == (actualTokens != nil), costKnown == (actualCost != nil) else {
            throw RPCFailure.invalidResponse
        }
        self.usageKnown = usageKnown; self.costKnown = costKnown
        overTokenLimit = overToken; overCostLimit = overCost
    }
}

public struct ProductCoordinationSnapshot: Equatable, Sendable {
    public let context: ProductInteractionBinding
    public let clientID: String
    public let binding: ProductSessionBinding
    public let workspaces: [ProductWorkspaceDescriptor]
    public let sessions: [ProductSessionBinding]
    public let inbox: [ProductAgentMessage]
    public let hasMoreMessages: Bool
    public let budget: ProductBudgetSnapshot?
    public var isOwner: Bool { binding.ownerID == clientID }
    public var mayWrite: Bool { binding.mayWrite(clientID: clientID) }
    public init(_ value: RPCValue, expectedScope: SessionScope, expectedRunID: String? = nil) throws {
        try ProductInteractionValidation.bounded(value)
        context = try ProductInteractionBinding(value["scope"], expectedScope: expectedScope,
                                                 expectedRunID: expectedRunID)
        clientID = try ProductCoordinationValidation.id(value["client_id"], client: true)
        binding = try ProductSessionBinding(value["binding"])
        workspaces = try ManagementValidation.rows(value["workspaces"], limit: 64,
                                                    parse: ProductWorkspaceDescriptor.init)
        sessions = try ManagementValidation.rows(value["sessions"], limit: 1000,
                                                   parse: ProductSessionBinding.init)
        inbox = try ManagementValidation.rows(value["inbox"]["messages"], limit: 100,
                                               parse: ProductAgentMessage.init)
        guard let more = value["inbox"]["has_more"].bool else { throw RPCFailure.invalidResponse }
        hasMoreMessages = more
        budget = value["budget"] == .null ? nil : try ProductBudgetSnapshot(value["budget"])
        guard value["inbox"]["acknowledgement_inferred"].bool == false,
              binding.scope == expectedScope, binding.workspaceID == context.workspaceID,
              binding.securityGeneration == context.generation,
              Set(workspaces.map(\.id)).count == workspaces.count,
              workspaces.allSatisfy({ $0.temporary == expectedScope.temporary }),
              workspaces.contains(where: { $0.id == binding.workspaceID }),
              Set(sessions.map(\.id)).count == sessions.count,
              sessions.allSatisfy({ $0.scope.temporary == expectedScope.temporary }),
              sessions.allSatisfy({ $0.ownerID == clientID || $0.members.contains { $0.id == clientID } }),
              sessions.contains(where: { $0.scope == binding.scope && $0.revision == binding.revision
                  && $0.workspaceID == binding.workspaceID && $0.ownerID == binding.ownerID }),
              Set(inbox.map(\.id)).count == inbox.count,
              inbox.allSatisfy({ $0.temporary == expectedScope.temporary
                  && $0.targetID == expectedScope.sessionID && $0.recipientID == clientID }) else {
            throw RPCFailure.invalidResponse
        }
        guard budget == nil || budget?.scope == expectedScope else { throw RPCFailure.invalidResponse }
    }
}
