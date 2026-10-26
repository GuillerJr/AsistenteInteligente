import Foundation

public final class ChannelReconciliationClient: Sendable {
    public typealias Request = MaintenanceClient.Request
    private let request: Request
    private let maintenance: MaintenanceClient
    public init(transport: LocalRPCClient) { request = { try await transport.call($0, params: $1) }; maintenance = MaintenanceClient(transport: transport) }
    public init(request: @escaping Request) { self.request = request; maintenance = MaintenanceClient(request: request) }
    public func close() async { await maintenance.close() }
    public func status() async throws -> MaintenanceStatus { try await maintenance.status() }
    public func pending() async throws -> MaintenancePending { try await maintenance.pending() }
    public func review(chatID: Int, revision: Int, enabled: Bool) async throws -> ChannelReconciliationReview {
        guard chatID > 0, chatID <= TelegramValidation.maximumIdentity, revision > 0 else { throw RPCFailure.configuration }
        let review = try await ChannelReconciliationReview(request("telegram.restore.review", ["chat_id": .number(Double(chatID)), "expected_revision": .number(Double(revision)), "enabled": .bool(enabled)]))
        guard review.chatID == chatID, review.revision == revision, review.enabledAfter == enabled else { throw RPCFailure.invalidResponse }; return review
    }
    public func submit(_ review: ChannelReconciliationReview) async throws -> MaintenanceOperation {
        guard review.expiresAt > Date() else { throw RPCFailure.configuration }
        let row = try await MaintenanceOperation(request(ChannelReconciliationValidation.effect, review.params))
        guard review.matches(row) else { throw RPCFailure.invalidResponse }; return row
    }
    public func inspect(chatID: Int) async throws -> ChannelReconciliationObservation {
        guard chatID > 0, chatID <= TelegramValidation.maximumIdentity else { throw RPCFailure.configuration }
        let value = try await ChannelReconciliationObservation(request("telegram.restore.inspect", ["chat_id": .number(Double(chatID))]))
        guard value.route.chatID == chatID else { throw RPCFailure.invalidResponse }; return value
    }
    public func decide(_ ticket: MaintenanceApproval, review: ChannelReconciliationReview, operation: MaintenanceOperation, allow: Bool) async throws -> Bool {
        guard review.matches(ticket, operation: operation) else { throw RPCFailure.configuration }
        var params = ticket.params; params["allow"] = .bool(allow)
        let result = try await request("maintenance.decide", params)
        try MaintenanceValidation.keys(result, ["accepted"])
        guard let accepted = result["accepted"].bool else { throw RPCFailure.invalidResponse }; return accepted
    }
    public func cancel(_ id: String) async throws -> MaintenanceOperation { try await maintenance.cancel(id) }
    public func cleanup(_ id: String) async throws -> MaintenanceOperation {
        guard ManagementValidation.identifier(id) else { throw RPCFailure.configuration }
        let value = try await MaintenanceOperation(request("maintenance.cleanup", ["operation_id": .string(id)]))
        guard value.id == id, value.kind == ChannelReconciliationValidation.effect else { throw RPCFailure.invalidResponse }; return value
    }
}
