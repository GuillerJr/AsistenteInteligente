import Foundation

/// Explicit native owner control. No provider call, model-tool settings, download on read or effect replay.
public final class MemoryIntelligenceClient: Sendable {
    private let request: MaintenanceClient.Request
    private let runtime: ProfileRuntimeGuard
    private let finish: @Sendable () async -> Void
    public init(transport: LocalRPCClient, runtime: ProfileRuntimeGuard) {
        request = { try await transport.call($0, params: $1) }; self.runtime = runtime
        finish = { await transport.close() }
    }
    public init(runtime: ProfileRuntimeGuard, request: @escaping MaintenanceClient.Request,
                close: @escaping @Sendable () async -> Void = {}) {
        self.request = request; self.runtime = runtime; finish = close
    }
    public func close() async { await finish() }
    private func call(_ suffix: String, scope: SessionScope, extra: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard ManagementValidation.identifier(scope.sessionID), Set(extra.keys).isDisjoint(with: ["session_id", "temporary"]) else { throw RPCFailure.configuration }
        try runtime.check(); try Task.checkCancellation()
        let result = try await request("memory.intelligence." + suffix, scope.params.merging(extra, uniquingKeysWith: { _, next in next }))
        try runtime.check(); try Task.checkCancellation(); return result
    }
    public func status(scope: SessionScope) async throws -> MemoryIntelligenceStatus {
        try await MemoryIntelligenceStatus(call("status", scope: scope), expectedScope: scope)
    }
    public func review(scope: SessionScope, policy: MemoryPolicySnapshot, settings: MemoryIntelligenceSettings) async throws -> MemoryPolicyChangeReview {
        guard policy.scope.matches(scope) else { throw RPCFailure.configuration }
        let envelope = try await call("policy.review", scope: scope, extra: ["settings": settings.value,
            "expected_revision": .number(Double(policy.revision)), "expected_digest": .string(policy.digest)])
        try MaintenanceValidation.keys(envelope, ["review"])
        let review = try MemoryPolicyChangeReview(envelope["review"])
        guard review.matches(policy), review.settings == settings else { throw RPCFailure.invalidResponse }; return review
    }
    public func apply(scope: SessionScope, review: MemoryPolicyChangeReview, now: Date = Date()) async throws -> MemoryIntelligenceStatus {
        guard review.expiresAt > now, review.scope.matches(scope) else { throw RPCFailure.configuration }
        let result = try await MemoryIntelligenceStatus(call("policy.apply", scope: scope, extra: ["review_id": .string(review.id), "review_digest": .string(review.digest)]), expectedScope: scope)
        guard result.policy.profileBinding == review.profileBinding, result.policy.scope == review.scope,
              result.policy.revision == review.beforeRevision + 1, result.policy.settings == review.settings else { throw RPCFailure.invalidResponse }; return result
    }
    public func prepareModel(scope: SessionScope, requestID: String) async throws -> MemoryIntelligenceJob {
        guard ManagementValidation.identifier(requestID) else { throw RPCFailure.configuration }
        let result = try await jobCall("model.prepare", scope: scope, extra: ["request_id": .string(requestID)])
        guard result.id == requestID, result.kind == .modelPrepare else { throw RPCFailure.invalidResponse }; return result
    }
    public func retireModelCopy(scope: SessionScope, requestID: String, copyID: String) async throws -> MemoryIntelligenceJob {
        guard ManagementValidation.identifier(requestID), ManagementValidation.identifier(copyID) else { throw RPCFailure.configuration }
        let result = try await jobCall("model.retire", scope: scope, extra: ["request_id": .string(requestID), "copy_id": .string(copyID)])
        guard result.id == requestID, result.kind == .modelRetire else { throw RPCFailure.invalidResponse }; return result
    }
    public func rebuild(scope: SessionScope, requestID: String, status: MemoryIntelligenceStatus) async throws -> MemoryIntelligenceJob {
        guard status.scope == scope, ManagementValidation.identifier(requestID), let model = status.model.fingerprint else { throw RPCFailure.configuration }
        let result = try await jobCall("index.rebuild", scope: scope, extra: ["request_id": .string(requestID),
            "expected_generation": .number(Double(status.index.sourceGeneration)),
            "expected_policy_revision": .number(Double(status.policy.revision)), "expected_model_fingerprint": .string(model)])
        guard result.id == requestID, result.kind == .indexRebuild, result.sourceGeneration == status.index.sourceGeneration,
              result.policyRevision == status.policy.revision else { throw RPCFailure.invalidResponse }; return result
    }
    public func verify(scope: SessionScope) async throws -> MemoryIndexVerification {
        try await MemoryIndexVerification(call("index.verify", scope: scope))
    }
    public func job(scope: SessionScope, id: String) async throws -> MemoryIntelligenceJob {
        try await identifiedJob("job.get", scope: scope, id: id)
    }
    public func cancel(scope: SessionScope, id: String) async throws -> MemoryIntelligenceJob {
        try await identifiedJob("job.cancel", scope: scope, id: id)
    }
    private func identifiedJob(_ suffix: String, scope: SessionScope, id: String) async throws -> MemoryIntelligenceJob {
        guard ManagementValidation.identifier(id) else { throw RPCFailure.configuration }
        let result = try await jobCall(suffix, scope: scope, extra: ["job_id": .string(id)])
        guard result.id == id else { throw RPCFailure.invalidResponse }; return result
    }
    private func jobCall(_ suffix: String, scope: SessionScope, extra: [String: RPCValue]) async throws -> MemoryIntelligenceJob {
        let envelope = try await call(suffix, scope: scope, extra: extra)
        try MaintenanceValidation.keys(envelope, ["job"]); return try MemoryIntelligenceJob(envelope["job"])
    }
}
