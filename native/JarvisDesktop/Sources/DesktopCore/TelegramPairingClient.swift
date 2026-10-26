import Foundation

@MainActor public final class TelegramPairingClient {
    private let request: MaintenanceClient.Request
    private var attemptedChallengeID: String?
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) } }
    init(request: @escaping MaintenanceClient.Request) { self.request = request }
    public func resetOpenAttempt() { attemptedChallengeID = nil }
    public func status() async throws -> TelegramPairingStatus { try await TelegramPairingStatus(request("telegram.pairing.status", [:])) }
    public func review(agentID: String) async throws -> TelegramPairingReview {
        guard ManagementValidation.identifier(agentID, agent: true) else { throw RPCFailure.configuration }
        let review = try await TelegramPairingReview(request("telegram.pairing.review", ["agent_id": .string(agentID)]))
        guard review.agentID == agentID else { throw RPCFailure.invalidResponse }
        return review
    }
    public func start(_ review: TelegramPairingReview, acknowledgeUpdateConsumption: Bool, confirmNoOtherConsumer: Bool) async throws -> TelegramPairingStatus {
        guard acknowledgeUpdateConsumption, confirmNoOtherConsumer else { throw RPCFailure.configuration }
        let result = try await TelegramPairingStatus(request("telegram.pairing.start", [
            "agent_id": .string(review.agentID), "expected_profile_revision": .number(Double(review.agentRevision)),
            "review_hash": .string(review.reviewHash), "acknowledge_update_consumption": .bool(true), "confirm_no_other_consumer": .bool(true)]))
        guard result.agentID == review.agentID, result.agentRevision == review.agentRevision,
              result.botID == review.botID, result.username == review.username else { throw RPCFailure.invalidResponse }
        return result
    }
    public func cancel() async throws -> TelegramPairingStatus { try await TelegramPairingStatus(request("telegram.pairing.cancel", [:])) }
    public func confirm(_ claim: TelegramPairingStatus) async throws -> TelegramSnapshot {
        guard claim.state == "claimed", let claimID = claim.claimID, let agent = claim.agentID, let revision = claim.agentRevision else {
            throw RPCFailure.configuration
        }
        let snapshot = try await TelegramSnapshot(request("telegram.pairing.confirm", ["claim_id": .string(claimID),
            "agent_id": .string(agent), "expected_profile_revision": .number(Double(revision))]))
        guard !snapshot.running, snapshot.botID == claim.botID,
              snapshot.routes.contains(where: { $0.chatID == claim.chatID && $0.agentID == agent }) else { throw RPCFailure.invalidResponse }
        return snapshot
    }

    public func confirmRestored(_ claim: TelegramPairingStatus, route: TelegramRouteSnapshot, snapshot: TelegramSnapshot) async throws -> TelegramSnapshot {
        guard claim.state == "claimed", let claimID = claim.claimID, let agent = claim.agentID, let revision = claim.agentRevision,
              claim.chatID == route.chatID, agent == route.agentID, route.enabled, snapshot.botID == claim.botID,
              !snapshot.running, snapshot.routes.contains(route),
              snapshot.restoredPairingRequiredChatIDs.contains(route.chatID),
              let expires = claim.expiresAt, expires > Date() else { throw RPCFailure.configuration }
        let result = try await TelegramSnapshot(request("telegram.restore.pairing.confirm", [
            "claim_id": .string(claimID), "agent_id": .string(agent), "expected_profile_revision": .number(Double(revision)),
            "chat_id": .number(Double(route.chatID)), "expected_route_revision": .number(Double(route.revision)),
            "configuration_hash": .string(snapshot.configurationHash)]))
        guard !result.running, result.botID == snapshot.botID, result.workspaceID == snapshot.workspaceID,
              result.configurationHash == snapshot.configurationHash, result.routes == snapshot.routes,
              Set(result.restoredPairingRequiredChatIDs) == Set(snapshot.restoredPairingRequiredChatIDs).subtracting([route.chatID]) else { throw RPCFailure.invalidResponse }
        return result
    }

    /// The sensitive response has no DTO/property accessible to a view. It is
    /// validated here and delivered directly to the native-only opener once.
    public func open(_ status: TelegramPairingStatus, using opener: any TelegramNativeURLOpener,
                     stillAllowed: @MainActor () -> Bool = { true }) async throws {
        guard status.state == "listening", let challenge = status.challengeID, let username = status.username else {
            throw RPCFailure.configuration
        }
        guard attemptedChallengeID != challenge else { throw TelegramPairingOpenFailure.alreadyAttempted }
        attemptedChallengeID = challenge
        do {
            guard !Task.isCancelled, stillAllowed() else { throw CancellationError() }
            guard opener.canOpenTelegram() else { throw TelegramPairingOpenFailure.unavailable }
            let result = try await request("telegram.pairing.open", ["challenge_id": .string(challenge)])
            guard let object = result.object, Set(object.keys) == ["challenge_id", "telegram_uri"],
                  result["challenge_id"].string == challenge, let text = result["telegram_uri"].string,
                  let url = Self.validatedURI(text, username: username) else { throw TelegramPairingOpenFailure.invalidLink }
            guard !Task.isCancelled, stillAllowed() else { throw CancellationError() }
            guard await opener.openTelegram(url) else { throw TelegramPairingOpenFailure.failed }
        } catch {
            // A failed opening terminates this challenge; never retry the URI,
            // use a browser fallback, or claim cancellation without its receipt.
            do { _ = try await cancel() }
            catch { throw TelegramPairingOpenFailure.cleanupUnconfirmed }
            throw (error as? TelegramPairingOpenFailure) ?? TelegramPairingOpenFailure.failed
        }
    }

    private static func validatedURI(_ text: String, username: String) -> URL? {
        guard text.utf8.count <= 256,
              let components = URLComponents(string: text), components.scheme == "tg", components.host == "resolve",
              components.user == nil, components.password == nil, components.port == nil,
              components.path.isEmpty, components.fragment == nil,
              let query = components.queryItems, query.count == 2,
              query[0].name == "domain", query[0].value == username, query[1].name == "start",
              let nonce = query[1].value,
              nonce.range(of: "^[A-Za-z0-9_-]{43}\\z", options: .regularExpression) != nil,
              text == "tg://resolve?domain=" + username + "&start=" + nonce else { return nil }
        return components.url
    }
}
