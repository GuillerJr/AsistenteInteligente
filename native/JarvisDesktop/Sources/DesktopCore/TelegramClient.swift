import Foundation

/// Explicit native controls only. This client never exposes Telegram setup as
/// model tools and never resends a mutation after an uncertain response.
public final class TelegramClient: Sendable {
    private let transport: LocalRPCClient
    public init(transport: LocalRPCClient) { self.transport = transport }
    public func close() async { await transport.close() }
    public func status() async throws -> TelegramSnapshot { try await TelegramSnapshot(transport.call("telegram.status")) }
    public func history() async throws -> TelegramHistorySnapshot { try await TelegramHistorySnapshot(transport.call("telegram.history")) }
    public func connect(token: String) async throws -> TelegramSnapshot {
        guard TelegramValidation.token(token) else { throw RPCFailure.configuration }
        return try await TelegramSnapshot(transport.call("telegram.connect", params: ["token": .string(token)]))
    }
    public func disconnect() async throws -> TelegramSnapshot { try await TelegramSnapshot(transport.call("telegram.disconnect")) }
    public func createRoute(chatID: Int, agentID: String) async throws -> TelegramSnapshot {
        guard chatID > 0, chatID <= TelegramValidation.maximumIdentity, ManagementValidation.identifier(agentID, agent: true) else {
            throw RPCFailure.configuration
        }
        return try await TelegramSnapshot(transport.call("telegram.routes.create", params: ["chat_id": .number(Double(chatID)), "agent_id": .string(agentID)]))
    }
    public func removeRoute(_ route: TelegramRouteSnapshot) async throws -> TelegramSnapshot {
        try await TelegramSnapshot(transport.call("telegram.routes.remove", params: ["chat_id": .number(Double(route.chatID)),
            "expected_revision": .number(Double(route.revision))]))
    }
    public func start(configurationHash: String) async throws -> TelegramSnapshot {
        guard ManagementValidation.hash(configurationHash) else { throw RPCFailure.configuration }
        return try await TelegramSnapshot(transport.call("telegram.start", params: ["configuration_hash": .string(configurationHash)]))
    }
    public func stop() async throws -> TelegramSnapshot { try await TelegramSnapshot(transport.call("telegram.stop")) }
}
