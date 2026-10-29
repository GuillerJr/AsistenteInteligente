import Foundation

@MainActor public final class TelegramGroupDiscoveryClient {
    private let request: MaintenanceClient.Request
    private var attemptedChallengeID: String?
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) } }
    init(request: @escaping MaintenanceClient.Request) { self.request = request }
    public func status() async throws -> TelegramGroupDiscoveryStatus { try await TelegramGroupDiscoveryStatus(request("telegram.group.discover.status", [:])) }
    public func cancel() async throws -> TelegramGroupDiscoveryStatus { try await TelegramGroupDiscoveryStatus(request("telegram.group.discover.cancel", [:])) }
    public func review(ownerChatID: Int, agentID: String, allowAttachments: Bool) async throws -> TelegramGroupDiscoveryReview {
        guard ownerChatID > 0, ownerChatID <= TelegramValidation.maximumIdentity, ManagementValidation.identifier(agentID, agent: true) else { throw RPCFailure.configuration }
        let result = try await TelegramGroupDiscoveryReview(request("telegram.group.discover.review", ["owner_chat_id": .number(Double(ownerChatID)), "agent_id": .string(agentID), "allow_attachments": .bool(allowAttachments)]))
        guard result.ownerChatID == ownerChatID, result.agentID == agentID, result.allowAttachments == allowAttachments else { throw RPCFailure.invalidResponse }
        return result
    }
    public func start(_ review: TelegramGroupDiscoveryReview, updateConsumption: Bool, noOtherConsumer: Bool) async throws -> TelegramGroupDiscoveryStatus {
        guard updateConsumption, noOtherConsumer else { throw RPCFailure.configuration }
        let result = try await TelegramGroupDiscoveryStatus(request("telegram.group.discover.start", ["owner_chat_id": .number(Double(review.ownerChatID)), "agent_id": .string(review.agentID), "allow_attachments": .bool(review.allowAttachments), "expected_profile_revision": .number(Double(review.agentRevision)), "review_hash": .string(review.reviewHash), "acknowledge_update_consumption": .bool(true), "confirm_no_other_consumer": .bool(true)]))
        guard result.matches(review), result.active else { throw RPCFailure.invalidResponse }
        return result
    }
    public func copy(_ status: TelegramGroupDiscoveryStatus, review: TelegramGroupDiscoveryReview,
                     using copier: any TelegramNativeCommandCopier, stillAllowed: @MainActor () -> Bool = { true }) async throws {
        guard status.state == "listening", status.matches(review), let challenge = status.challengeID,
              let expires = status.expiresAt, expires > Date() else { throw RPCFailure.configuration }
        guard attemptedChallengeID != challenge else { throw TelegramGroupCommandFailure.alreadyAttempted }
        attemptedChallengeID = challenge
        do {
            guard !Task.isCancelled, stillAllowed() else { throw CancellationError() }
            let value = try await request("telegram.group.discover.command", ["challenge_id": .string(challenge)])
            try MaintenanceValidation.keys(value, ["challenge_id", "command"])
            let prefix = "/jarvis_pair@" + review.username + " "
            guard value["challenge_id"] == .string(challenge), let command = value["command"].string,
                  command.utf8.count <= 128, command.hasPrefix(prefix),
                  command.dropFirst(prefix.count).range(of: "^[A-Za-z0-9_-]{43}\\z", options: .regularExpression) != nil else { throw TelegramGroupCommandFailure.invalidCommand }
            guard !Task.isCancelled, stillAllowed(), expires > Date() else { throw CancellationError() }
            guard copier.copyTelegramCommand(command) else { throw TelegramGroupCommandFailure.copyFailed }
        } catch {
            do { _ = try await cancel() } catch { throw TelegramGroupCommandFailure.cleanupUnconfirmed }
            throw (error as? TelegramGroupCommandFailure) ?? TelegramGroupCommandFailure.copyFailed
        }
    }
    public func confirm(_ status: TelegramGroupDiscoveryStatus, review: TelegramGroupDiscoveryReview,
                        snapshot: TelegramSnapshot) async throws -> TelegramSnapshot {
        guard status.state == "claimed", status.matches(review), let claim = status.claimID,
              let expires = status.expiresAt, expires > Date(), let chat = status.chatID, let kind = status.chatType,
              snapshot.connected, !snapshot.running, snapshot.botID == review.botID, snapshot.username == review.username,
              snapshot.workspaceID == review.workspaceID, snapshot.configurationHash == review.configurationHash,
              !snapshot.routes.contains(where: { $0.chatID == chat }) else { throw RPCFailure.configuration }
        let result = try await TelegramSnapshot(request("telegram.group.discover.confirm", ["claim_id": .string(claim), "expected_profile_revision": .number(Double(review.agentRevision)), "configuration_hash": .string(snapshot.configurationHash)]))
        let policy = try TelegramRoutePolicy(chatType: kind, allowedSenderIDs: [review.ownerChatID], messageThreadID: status.messageThreadID, allowAttachments: review.allowAttachments)
        guard result.connected, !result.running, result.botID == snapshot.botID, result.workspaceID == snapshot.workspaceID,
              result.configurationHash != snapshot.configurationHash, result.routes.count == snapshot.routes.count + 1,
              snapshot.routes.allSatisfy({ result.routes.contains($0) }),
              let route = result.routes.first(where: { $0.chatID == chat }), route.agentID == review.agentID,
              route.revision == 1, route.enabled, route.policy == policy,
              !snapshot.routes.contains(where: { $0.sessionID == route.sessionID }),
              !result.restoredPairingRequiredChatIDs.contains(chat) else { throw RPCFailure.invalidResponse }
        return result
    }
}
