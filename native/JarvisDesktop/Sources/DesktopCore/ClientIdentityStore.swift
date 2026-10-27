import Foundation
import Observation

@MainActor @Observable public final class ClientIdentityStore {
    public private(set) var snapshot: LocalClientsSnapshot?
    public private(set) var review: LocalClientPairReview?
    public private(set) var isRequesting = false
    public private(set) var responseUnconfirmed = false
    public private(set) var errorMessage: String?
    public private(set) var observationNotice: String?
    @ObservationIgnored private var client: ClientIdentityClient?
    @ObservationIgnored private var closeTransport: @Sendable () async -> Void = {}
    @ObservationIgnored private var version = 0
    @ObservationIgnored private var pendingID: String?
    @ObservationIgnored private var pendingScope: String?
    @ObservationIgnored private var pendingRevoke = false
    public var blocksInteraction: Bool { isRequesting || responseUnconfirmed || review != nil }
    public init() {}
    public func configure(_ transport: LocalRPCClient) {
        configure(ClientIdentityClient(transport: transport), close: { await transport.close() })
    }
    public func configure(_ client: ClientIdentityClient, close: @escaping @Sendable () async -> Void = {}) {
        version += 1; self.client = client; closeTransport = close; snapshot = nil; review = nil
        isRequesting = false; responseUnconfirmed = false; errorMessage = nil; observationNotice = nil
        pendingID = nil; pendingScope = nil; pendingRevoke = false
    }
    public func close() async { version += 1; await closeTransport(); client = nil; review = nil; isRequesting = false }
    public func refresh() async {
        guard let client, !isRequesting else { return }
        let current = version; isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            let observed = try await client.status()
            guard current == version, !Task.isCancelled else { return }
            if let review, review.expectedRevision != observed.revision || snapshot?.serverID != observed.serverID {
                self.review = nil
            }
            snapshot = observed; errorMessage = nil
            if responseUnconfirmed, let id = pendingID,
               let row = observed.clients.first(where: { $0.clientID == id }),
               row.revoked == pendingRevoke, pendingRevoke || row.scope == pendingScope {
                responseUnconfirmed = false
                observationNotice = "El registro actual refleja la decisión. Esta consulta no confirma la recepción de la respuesta original ni repite la operación."
            }
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func prepare(publicKey: String, scope: String) async {
        guard let client, let snapshot, !blocksInteraction else { return }
        let current = version; isRequesting = true; observationNotice = nil
        defer { if current == version { isRequesting = false } }
        do {
            let value = try await client.review(publicKey: publicKey, scope: scope, snapshot: snapshot)
            guard current == version, !Task.isCancelled else { return }; review = value; errorMessage = nil
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func discardReview() { guard !isRequesting else { return }; review = nil }
    public func admit() async {
        guard let client, let snapshot, let review, !isRequesting, !responseUnconfirmed, review.expiresAt > Date() else { return }
        let current = version; isRequesting = true; self.review = nil
        pendingID = review.clientID; pendingScope = review.scope; pendingRevoke = false
        defer { if current == version { isRequesting = false } }
        do {
            let value = try await client.admit(review, snapshot: snapshot)
            guard current == version else { return }; self.snapshot = value; errorMessage = nil
        } catch { if current == version { responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error); errorMessage = MaintenancePresentation.error(error) } }
    }
    public func revoke(_ row: LocalClientRow) async {
        guard let client, let snapshot, !blocksInteraction else { return }
        let current = version; isRequesting = true; pendingID = row.clientID; pendingScope = row.scope; pendingRevoke = true
        defer { if current == version { isRequesting = false } }
        do {
            _ = try await client.revoke(row, snapshot: snapshot)
            let observed = try await client.status()
            guard current == version else { return }
            guard observed.profileBinding == snapshot.profileBinding,
                  observed.clients.contains(where: { $0.clientID == row.clientID && $0.revoked }) else { throw RPCFailure.invalidResponse }
            self.snapshot = observed; errorMessage = nil
        } catch { if current == version { responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error); errorMessage = MaintenancePresentation.error(error) } }
    }
    public func retainUnconfirmed() {
        guard !isRequesting, responseUnconfirmed else { return }
        responseUnconfirmed = false; snapshot = nil
        observationNotice = "Decisión conservada sin confirmar. Consulta el registro antes de preparar otra."
    }
}
