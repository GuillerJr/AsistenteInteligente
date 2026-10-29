import Foundation

@MainActor public final class TelegramGroupPairingClient {
    private let request: MaintenanceClient.Request
    private var attemptedChallengeID: String?
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) } }
    init(request: @escaping MaintenanceClient.Request) { self.request = request }
    public func status() async throws -> TelegramGroupPairingStatus { try await TelegramGroupPairingStatus(request("telegram.group.pairing.status", [:])) }
    public func cancel() async throws -> TelegramGroupPairingStatus { try await TelegramGroupPairingStatus(request("telegram.group.pairing.cancel", [:])) }
    public func review(route: TelegramRouteSnapshot) async throws -> TelegramGroupPairingReview {
        guard route.chatType != .privateChat else { throw RPCFailure.configuration }
        let result = try await TelegramGroupPairingReview(request("telegram.group.pairing.review", ["chat_id": .number(Double(route.chatID))]))
        guard result.chatID == route.chatID, result.routeRevision == route.revision, result.base.agentID == route.agentID, result.policy == route.policy else { throw RPCFailure.invalidResponse }
        return result
    }
    public func start(_ review: TelegramGroupPairingReview, updateConsumption: Bool, noOtherConsumer: Bool) async throws -> TelegramGroupPairingStatus {
        guard updateConsumption, noOtherConsumer else { throw RPCFailure.configuration }
        let result = try await TelegramGroupPairingStatus(request("telegram.group.pairing.start", ["chat_id": .number(Double(review.chatID)),
            "expected_route_revision": .number(Double(review.routeRevision)), "expected_profile_revision": .number(Double(review.base.agentRevision)),
            "review_hash": .string(review.base.reviewHash), "acknowledge_update_consumption": .bool(true), "confirm_no_other_consumer": .bool(true)]))
        guard result.targetChatID == review.chatID, result.routeRevision == review.routeRevision,
              result.base.agentID == review.base.agentID, result.base.agentRevision == review.base.agentRevision,
              result.base.botID == review.base.botID, result.base.username == review.base.username,
              result.chatType == review.policy.chatType, result.allowedSenderIDs == review.policy.allowedSenderIDs,
              result.messageThreadID == review.policy.messageThreadID else { throw RPCFailure.invalidResponse }
        return result
    }
    public func confirm(_ claim: TelegramGroupPairingStatus, route: TelegramRouteSnapshot, snapshot: TelegramSnapshot) async throws -> TelegramSnapshot {
        guard claim.state == "claimed", let claimID = claim.base.claimID, let revision = claim.base.agentRevision,
              let expires = claim.base.expiresAt, expires > Date(), claim.chatID == route.chatID, claim.routeRevision == route.revision,
              claim.base.agentID == route.agentID, claim.base.botID == snapshot.botID, route.enabled, !snapshot.running,
              claim.chatType == route.chatType, claim.allowedSenderIDs == route.policy.allowedSenderIDs, claim.messageThreadID == route.policy.messageThreadID,
              snapshot.routes.contains(route), snapshot.restoredPairingRequiredChatIDs.contains(route.chatID) else { throw RPCFailure.configuration }
        let result = try await TelegramSnapshot(request("telegram.group.pairing.confirm", ["claim_id": .string(claimID), "chat_id": .number(Double(route.chatID)),
            "expected_route_revision": .number(Double(route.revision)), "expected_profile_revision": .number(Double(revision)), "configuration_hash": .string(snapshot.configurationHash)]))
        guard !result.running, result.routes == snapshot.routes, result.configurationHash == snapshot.configurationHash,
              result.botID == snapshot.botID, result.workspaceID == snapshot.workspaceID,
              Set(result.restoredPairingRequiredChatIDs) == Set(snapshot.restoredPairingRequiredChatIDs).subtracting([route.chatID]) else { throw RPCFailure.invalidResponse }
        return result
    }
    /// A native owner action copies the one-use command directly. Never return
    /// it to a view, log, model message, generic review or automatic Telegram send.
    public func copy(_ status: TelegramGroupPairingStatus, using copier: any TelegramNativeCommandCopier,
                     stillAllowed: @MainActor () -> Bool = { true }) async throws {
        guard status.state == "listening", let challenge = status.base.challengeID, let username = status.base.username,
              let expires = status.base.expiresAt, expires > Date() else { throw RPCFailure.configuration }
        guard attemptedChallengeID != challenge else { throw TelegramGroupCommandFailure.alreadyAttempted }
        attemptedChallengeID = challenge
        do {
            guard !Task.isCancelled, stillAllowed() else { throw CancellationError() }
            let result = try await request("telegram.group.pairing.command", ["challenge_id": .string(challenge)])
            guard let object = result.object, Set(object.keys) == ["challenge_id", "command"], result["challenge_id"].string == challenge,
                  let command = result["command"].string, command.utf8.count <= 128,
                  command.hasPrefix("/jarvis_pair@" + username + " "),
                  command.dropFirst(("/jarvis_pair@" + username + " ").count).range(of: "^[A-Za-z0-9_-]{43}\\z", options: .regularExpression) != nil else { throw TelegramGroupCommandFailure.invalidCommand }
            guard !Task.isCancelled, stillAllowed(), expires > Date() else { throw CancellationError() }
            guard copier.copyTelegramCommand(command) else { throw TelegramGroupCommandFailure.copyFailed }
        } catch {
            do { _ = try await cancel() } catch { throw TelegramGroupCommandFailure.cleanupUnconfirmed }
            throw (error as? TelegramGroupCommandFailure) ?? TelegramGroupCommandFailure.copyFailed
        }
    }
}
