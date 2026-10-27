import Foundation

public final class ClientIdentityClient: Sendable {
    private let request: MaintenanceClient.Request
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) } }
    public init(request: @escaping MaintenanceClient.Request) { self.request = request }
    public func status() async throws -> LocalClientsSnapshot {
        try await LocalClientsSnapshot(request("clients.status", [:]))
    }
    public func review(publicKey: String, scope: String, snapshot: LocalClientsSnapshot) async throws -> LocalClientPairReview {
        let expectedID = try LocalClientPairReview.clientID(publicKey: publicKey)
        guard ["owner", "read"].contains(scope), !snapshot.clients.contains(where: { $0.clientID == expectedID }) else { throw RPCFailure.configuration }
        let review = try await LocalClientPairReview(request("clients.review", [
            "public_key": .string(publicKey), "scope": .string(scope), "expected_revision": .number(Double(snapshot.revision))]))
        guard review.clientID == expectedID, review.scope == scope,
              review.expectedRevision == snapshot.revision else { throw RPCFailure.invalidResponse }
        return review
    }
    public func admit(_ review: LocalClientPairReview, snapshot: LocalClientsSnapshot) async throws -> LocalClientsSnapshot {
        guard review.expiresAt > Date(), review.expectedRevision == snapshot.revision,
              !snapshot.clients.contains(where: { $0.clientID == review.clientID }) else { throw RPCFailure.configuration }
        let value = try await LocalClientsSnapshot(request("clients.admit", review.params))
        guard value.profileBinding == snapshot.profileBinding, value.serverID == snapshot.serverID,
              value.clientID == snapshot.clientID, value.revision == snapshot.revision + 1,
              value.clients.count == snapshot.clients.count + 1,
              value.clients.contains(where: { $0.clientID == review.clientID && $0.scope == review.scope && !$0.revoked }),
              snapshot.clients.allSatisfy(value.clients.contains) else { throw RPCFailure.invalidResponse }
        return value
    }
    public func revoke(_ row: LocalClientRow, snapshot: LocalClientsSnapshot) async throws -> LocalClientMutationReply {
        guard snapshot.clients.contains(row), !row.revoked,
              row.scope != "owner" || snapshot.clients.filter({ $0.scope == "owner" && !$0.revoked }).count > 1 else { throw RPCFailure.configuration }
        let value = try await LocalClientMutationReply(request("clients.revoke", [
            "client_id": .string(row.clientID), "expected_revision": .number(Double(snapshot.revision))]))
        guard value.revision == snapshot.revision + 1 else { throw RPCFailure.invalidResponse }
        return value
    }
}
