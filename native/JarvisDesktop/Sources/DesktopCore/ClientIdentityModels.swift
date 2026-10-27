import CryptoKit
import Foundation

public struct LocalClientRow: Sendable, Equatable, Identifiable {
    public let clientID: String
    public let scope: String
    public let revoked: Bool
    public var id: String { clientID }
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["client_id", "scope", "revoked"])
        guard let id = value["client_id"].string, ManagementValidation.hash(id),
              let scope = value["scope"].string, ["owner", "read"].contains(scope),
              let revoked = value["revoked"].bool else { throw RPCFailure.invalidResponse }
        clientID = id; self.scope = scope; self.revoked = revoked
    }
}

public struct LocalClientsSnapshot: Sendable, Equatable {
    public let profileBinding: String
    public let serverID: String
    public let clientID: String
    public let revision: Int
    public let clients: [LocalClientRow]
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["profile_binding", "server_id", "client_id", "revision", "clients"])
        guard let binding = value["profile_binding"].string, ManagementValidation.hash(binding),
              let server = value["server_id"].string, ManagementValidation.hash(server),
              let client = value["client_id"].string, ManagementValidation.hash(client),
              let revision = value["revision"].integer, revision >= 1,
              let rows = value["clients"].array, rows.count <= 32 else { throw RPCFailure.invalidResponse }
        clients = try rows.map(LocalClientRow.init)
        guard Set(clients.map(\.clientID)).count == clients.count,
              clients.contains(where: { $0.clientID == client && $0.scope == "owner" && !$0.revoked }) else { throw RPCFailure.invalidResponse }
        profileBinding = binding; serverID = server; clientID = client; self.revision = revision
    }
}

public struct LocalClientPairReview: Sendable, Equatable {
    public let reviewID: String
    public let reviewDigest: String
    public let clientID: String
    public let scope: String
    public let expectedRevision: Int
    public let expiresAt: Date
    public init(_ value: RPCValue, now: Date = Date()) throws {
        try MaintenanceValidation.keys(value, ["review_id", "review_digest", "client_id", "scope", "expected_revision", "expires_in_seconds"])
        guard let id = value["review_id"].string, ManagementValidation.identifier(id),
              let digest = value["review_digest"].string, ManagementValidation.hash(digest),
              let client = value["client_id"].string, ManagementValidation.hash(client),
              let scope = value["scope"].string, ["owner", "read"].contains(scope),
              let revision = value["expected_revision"].integer, revision >= 1,
              let ttl = value["expires_in_seconds"].integer, ttl > 0, ttl <= 120 else { throw RPCFailure.invalidResponse }
        reviewID = id; reviewDigest = digest; clientID = client; self.scope = scope
        expectedRevision = revision; expiresAt = now.addingTimeInterval(TimeInterval(ttl))
    }
    public var params: [String: RPCValue] { ["review_id": .string(reviewID), "review_digest": .string(reviewDigest)] }
    public static func clientID(publicKey: String) throws -> String {
        guard ManagementValidation.hash(publicKey) else { throw RPCFailure.configuration }
        var bytes = Data()
        var index = publicKey.startIndex
        while index < publicKey.endIndex {
            let next = publicKey.index(index, offsetBy: 2)
            guard let byte = UInt8(publicKey[index..<next], radix: 16) else { throw RPCFailure.configuration }
            bytes.append(byte); index = next
        }
        return SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }
}

public struct LocalClientMutationReply: Sendable, Equatable {
    public let revision: Int
    public init(_ value: RPCValue) throws {
        try MaintenanceValidation.keys(value, ["revoked", "revision"])
        guard value["revoked"].bool == true, let revision = value["revision"].integer,
              revision >= 2 else { throw RPCFailure.invalidResponse }
        self.revision = revision
    }
}
