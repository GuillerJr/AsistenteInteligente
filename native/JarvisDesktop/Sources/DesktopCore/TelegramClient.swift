import Foundation

/// Explicit native controls only. This client never exposes Telegram setup as
/// model tools and never resends a mutation after an uncertain response.
public final class TelegramClient: Sendable {
    private let request: MaintenanceClient.Request
    private let finish: @Sendable () async -> Void
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) }; finish = { await transport.close() } }
    init(request: @escaping MaintenanceClient.Request) { self.request = request; finish = {} }
    public func close() async { await finish() }
    public func status() async throws -> TelegramSnapshot { try await TelegramSnapshot(request("telegram.status", [:])) }
    public func history() async throws -> TelegramHistorySnapshot { try await TelegramHistorySnapshot(request("telegram.history", [:])) }
    public func connect(token: String) async throws -> TelegramSnapshot {
        guard TelegramValidation.token(token) else { throw RPCFailure.configuration }
        return try await TelegramSnapshot(request("telegram.connect", ["token": .string(token)]))
    }
    public func disconnect() async throws -> TelegramSnapshot { try await TelegramSnapshot(request("telegram.disconnect", [:])) }
    public func createRoute(chatID: Int, agentID: String) async throws -> TelegramSnapshot {
        guard chatID > 0, chatID <= TelegramValidation.maximumIdentity, ManagementValidation.identifier(agentID, agent: true) else {
            throw RPCFailure.configuration
        }
        return try await TelegramSnapshot(request("telegram.routes.create", ["chat_id": .number(Double(chatID)), "agent_id": .string(agentID)]))
    }
    public func createRoute(chatID: Int, agentID: String, policy: TelegramRoutePolicy) async throws -> TelegramSnapshot {
        guard TelegramValidation.routeIdentity(.number(Double(chatID)), type: policy.chatType) == chatID,
              ManagementValidation.identifier(agentID, agent: true) else { throw RPCFailure.configuration }
        var params = policy.params; params["chat_id"] = .number(Double(chatID)); params["agent_id"] = .string(agentID)
        let result = try await TelegramSnapshot(request("telegram.routes.create", params))
        guard !result.running, result.routes.contains(where: { $0.chatID == chatID && $0.agentID == agentID && $0.policy == policy }) else { throw RPCFailure.invalidResponse }
        return result
    }
    public func updateRoute(_ route: TelegramRouteSnapshot, policy: TelegramRoutePolicy, enabled: Bool) async throws -> TelegramSnapshot {
        guard TelegramValidation.routeIdentity(.number(Double(route.chatID)), type: policy.chatType) == route.chatID else { throw RPCFailure.configuration }
        var params = policy.params; params["chat_id"] = .number(Double(route.chatID)); params["expected_revision"] = .number(Double(route.revision)); params["enabled"] = .bool(enabled)
        let result = try await TelegramSnapshot(request("telegram.routes.update", params))
        guard route.revision < Int.max, !result.running, result.routes.contains(where: { $0.chatID == route.chatID && $0.agentID == route.agentID && $0.sessionID == route.sessionID && $0.revision == route.revision + 1 && $0.policy == policy && $0.enabled == enabled }) else { throw RPCFailure.invalidResponse }
        return result
    }
    public func cancelEvent(_ event: TelegramEventSnapshot) async throws {
        guard event.canCancel, let run = event.runID, let revision = event.routeRevision else { throw RPCFailure.configuration }
        let result = try await request("telegram.events.cancel", ["epoch": .number(Double(event.epoch)), "update_id": .number(Double(event.updateID)),
            "expected_run_id": .string(run), "expected_route_revision": .number(Double(revision))])
        guard let object = result.object, Set(object.keys) == ["run_id", "cancel_requested", "automatic_replay"],
              result["run_id"].string == run, result["cancel_requested"].bool == true, result["automatic_replay"].bool == false else { throw RPCFailure.invalidResponse }
    }
    public func removeRoute(_ route: TelegramRouteSnapshot) async throws -> TelegramSnapshot {
        try await TelegramSnapshot(request("telegram.routes.remove", ["chat_id": .number(Double(route.chatID)),
            "expected_revision": .number(Double(route.revision))]))
    }
    public func start(configurationHash: String) async throws -> TelegramSnapshot {
        guard ManagementValidation.hash(configurationHash) else { throw RPCFailure.configuration }
        return try await TelegramSnapshot(request("telegram.start", ["configuration_hash": .string(configurationHash)]))
    }
    public func stop() async throws -> TelegramSnapshot { try await TelegramSnapshot(request("telegram.stop", [:])) }
}
