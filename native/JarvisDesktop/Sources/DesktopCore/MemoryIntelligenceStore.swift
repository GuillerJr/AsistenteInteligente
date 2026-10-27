import Foundation
import Observation

public enum MemoryIntelligenceConfirmationKind: Equatable, Sendable { case prepareModel, retireModelCopy, rebuildIndex }
public struct MemoryIntelligenceConfirmation: Identifiable, Sendable {
    public let id: UUID
    public let kind: MemoryIntelligenceConfirmationKind
    public let sourceGeneration: Int
    public let expiresAt: Date
    public let previousCopy: MemoryPreviousModelCopy?
    let epoch: Int
    let policyDigest: String
    let modelFingerprint: String?
}

/// Metadata and unsaved drafts only. Backend policy remains the single authority.
@MainActor @Observable
public final class MemoryIntelligenceStore {
    public private(set) var scope: SessionScope?
    public private(set) var status: MemoryIntelligenceStatus?
    public private(set) var job: MemoryIntelligenceJob?
    public var draft = MemoryIntelligenceSettings()
    public private(set) var review: MemoryPolicyChangeReview?
    public private(set) var confirmation: MemoryIntelligenceConfirmation?
    public private(set) var verification: MemoryIndexVerification?
    public private(set) var errorMessage: String?
    public private(set) var isRequesting = false
    public private(set) var isRefreshing = false
    public private(set) var responseUnconfirmed = false
    public private(set) var pollingPaused = false
    @ObservationIgnored public var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored public var isContextCurrent: (@MainActor (SessionScope, String) -> Bool)?
    @ObservationIgnored private let runtime: ProfileRuntimeGuard
    @ObservationIgnored private var client: MemoryIntelligenceClient?
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored private var contextKey = ""
    @ObservationIgnored private var visibleViews = Set<UUID>()
    @ObservationIgnored private var monitoringRequested = false
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var pendingJobID: String?
    @ObservationIgnored private var pendingPolicy: MemoryPolicyChangeReview?
    @ObservationIgnored private let pause: @Sendable () async throws -> Void
    @ObservationIgnored private let maxPolls: Int
    public init(runtime: ProfileRuntimeGuard, maxPolls: Int = 240,
                pause: @escaping @Sendable () async throws -> Void = { try await Task.sleep(for: .milliseconds(500)) }) {
        self.runtime = runtime; self.maxPolls = min(240, max(1, maxPolls)); self.pause = pause
    }
    public var isBusy: Bool { isRequesting || isRefreshing }
    public var monitorPaused: Bool { isBusy }
    private var hasCurrentContext: Bool { runtime.isActive && scope.map { isContextCurrent?($0, contextKey) ?? true } == true }
    public var mayReview: Bool { hasCurrentContext && client != nil && status != nil && !isBusy && !responseUnconfirmed }
    public var mayStartJob: Bool { mayReview && review == nil && job?.settled != false && confirmation == nil }
    public var mayApply: Bool { mayReview && review.map { $0.expiresAt > Date() && $0.matches(status!.policy) } == true }
    public var mayCancel: Bool { hasCurrentContext && !isBusy && job.map { [.queued, .running].contains($0.phase) } == true }
    public var blocksInteraction: Bool { isRequesting || responseUnconfirmed || job?.settled == false }
    public var hasUnsavedChanges: Bool { status.map { $0.policy.settings != draft } ?? false }

    public func configure(_ transport: LocalRPCClient) { configure(MemoryIntelligenceClient(transport: transport, runtime: runtime)) }
    public func configure(_ client: MemoryIntelligenceClient) {
        reset(); self.client = client
    }
    /// Caller must change contextKey for profile, agent or workspace changes, even if the chat ID is unchanged.
    public func select(scope: SessionScope?, contextKey: String) {
        guard self.scope != scope || self.contextKey != contextKey else { return }
        reset(); self.scope = scope; self.contextKey = contextKey
    }
    public func close() async {
        let captured = client
        reset(); scope = nil; contextKey = ""; client = nil
        await captured?.close()
    }
    private func reset() {
        epoch += 1; polling?.cancel(); polling = nil
        status = nil; job = nil; draft = MemoryIntelligenceSettings(); review = nil; confirmation = nil; verification = nil
        pendingJobID = nil; pendingPolicy = nil; errorMessage = nil; responseUnconfirmed = false
        isRequesting = false; isRefreshing = false; pollingPaused = false
        monitoringRequested = !visibleViews.isEmpty
    }
    private func check(_ version: Int, scope: SessionScope) throws {
        try runtime.check(); try Task.checkCancellation()
        guard epoch == version, self.scope == scope, hasCurrentContext else { throw CancellationError() }
    }
    private func beforeReadOrEffect(_ version: Int, scope: SessionScope) async throws {
        try check(version, scope: scope)
        try await waitForSupervisionIdle?()
        try check(version, scope: scope)
    }
    private func accept(_ next: MemoryIntelligenceStatus) throws {
        if let before = status {
            guard next.scope == before.scope, next.policy.sameOwner(as: before.policy),
                  next.policy.revision >= before.policy.revision else { throw RPCFailure.invalidResponse }
        }
        let changed = status?.policy.digest != next.policy.digest
        let confirmationChanged = confirmation.map { $0.policyDigest != next.policy.digest
            || $0.sourceGeneration != next.index.sourceGeneration || $0.modelFingerprint != next.model.fingerprint
            || $0.previousCopy != next.model.previousCopy } == true
        status = next; job = next.currentJob
        if changed { draft = next.policy.settings; review = nil; confirmation = nil; verification = nil }
        else if confirmationChanged { confirmation = nil }
    }
    private func report(_ error: Error, mutation: Bool) {
        if error is CancellationError && !mutation { return }
        if mutation { responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error) }
        if responseUnconfirmed {
            errorMessage = "No se confirmó la respuesta. No se repetirá la operación; consulta su estado."
        } else if let failure = error as? RPCFailure, case .remote(_, let code) = failure,
                  code.range(of: "^[a-z][a-z0-9_]{0,127}\\z", options: .regularExpression) != nil {
            errorMessage = "La memoria local informó: \(code)."
        } else { errorMessage = "No se pudo confirmar el estado de la memoria local. Actualiza para comprobarlo." }
    }
    public func refresh() async {
        guard let client, let scope, hasCurrentContext, !isBusy else { return }
        let version = epoch; isRefreshing = true
        defer { if epoch == version { isRefreshing = false } }
        do {
            try await beforeReadOrEffect(version, scope: scope)
            let next = try await client.status(scope: scope); try check(version, scope: scope)
            try accept(next)
            if let id = pendingJobID {
                let recovered = try await client.job(scope: scope, id: id); try check(version, scope: scope)
                if !recovered.settled || next.currentJob?.settled != false { job = recovered }
                pendingJobID = nil; responseUnconfirmed = false
            } else if let pending = pendingPolicy {
                if next.policy.profileBinding == pending.profileBinding, next.policy.scope == pending.scope,
                   next.policy.revision == pending.beforeRevision + 1,
                   next.policy.settings == pending.settings {
                    pendingPolicy = nil; responseUnconfirmed = false
                } else if next.policy.digest == pending.beforeDigest {
                    // The publication may still be settling. A read is not proof that it was rejected.
                    responseUnconfirmed = true
                }
            } else { responseUnconfirmed = false }
            if !responseUnconfirmed { errorMessage = nil }
        } catch { if epoch == version { report(error, mutation: false) } }
        startPollingIfNeeded()
    }
    public func discardReview() { guard !isRequesting else { return }; review = nil; confirmation = nil }
    public func reviewPolicy() async { await reviewPolicy(settings: draft) }
    public func revokeAutomation() async {
        guard let status else { return }
        await reviewPolicy(settings: MemoryIntelligenceSettings(mode: status.policy.settings.mode))
    }
    private func reviewPolicy(settings: MemoryIntelligenceSettings) async {
        guard let client, let scope, let status, mayReview else { return }
        let version = epoch; isRequesting = true; review = nil; confirmation = nil; errorMessage = nil
        defer { if epoch == version { isRequesting = false } }
        do {
            try await beforeReadOrEffect(version, scope: scope)
            let result = try await client.review(scope: scope, policy: status.policy, settings: settings)
            try check(version, scope: scope)
            guard self.status?.policy.digest == status.policy.digest else { throw RPCFailure.configuration }
            review = result
        } catch { if epoch == version { report(error, mutation: false) } }
    }
    public func applyPolicy() async {
        guard let client, let scope, let selected = review, mayApply else { return }
        let version = epoch; isRequesting = true; errorMessage = nil; pendingPolicy = selected
        var submissionAttempted = false
        defer { if epoch == version { isRequesting = false } }
        do {
            try await beforeReadOrEffect(version, scope: scope)
            submissionAttempted = true
            let result = try await client.apply(scope: scope, review: selected); try check(version, scope: scope)
            try accept(result); pendingPolicy = nil; responseUnconfirmed = false
        } catch {
            if epoch == version { report(error, mutation: submissionAttempted); if !responseUnconfirmed { pendingPolicy = nil } }
        }
        startPollingIfNeeded()
    }
    public func requestModelPreparation() {
        guard scope?.temporary == false else { return }
        makeConfirmation(.prepareModel)
    }
    public func requestPreviousModelCopyRetirement() {
        guard scope?.temporary == false, status?.model.previousCopy != nil else { return }
        makeConfirmation(.retireModelCopy)
    }
    public func requestIndexRebuild() {
        guard status?.model.available == true, status?.model.runtimeAvailable == true,
              status?.index.isOpened == true else { return }
        makeConfirmation(.rebuildIndex)
    }
    private func makeConfirmation(_ kind: MemoryIntelligenceConfirmationKind) {
        guard mayStartJob, let status else { return }
        confirmation = MemoryIntelligenceConfirmation(id: UUID(), kind: kind, sourceGeneration: status.index.sourceGeneration,
            expiresAt: Date().addingTimeInterval(120), previousCopy: status.model.previousCopy,
            epoch: epoch, policyDigest: status.policy.digest, modelFingerprint: status.model.fingerprint)
    }
    public func prepareModel() async { await startJob(.prepareModel) }
    public func retirePreviousModelCopy() async { await startJob(.retireModelCopy) }
    public func rebuildIndex() async { await startJob(.rebuildIndex) }
    private func startJob(_ kind: MemoryIntelligenceConfirmationKind) async {
        guard let client, let scope, let status, let selected = confirmation,
              kind == .rebuildIndex || !scope.temporary,
              selected.kind == kind, selected.epoch == epoch, selected.expiresAt > Date(),
              selected.policyDigest == status.policy.digest, selected.sourceGeneration == status.index.sourceGeneration,
              selected.modelFingerprint == status.model.fingerprint, hasCurrentContext,
              selected.previousCopy == status.model.previousCopy,
              !isBusy, !responseUnconfirmed, job?.settled != false else { return }
        let version = epoch; isRequesting = true; errorMessage = nil; confirmation = nil
        let requestID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        pendingJobID = requestID
        var submissionAttempted = false
        defer { if epoch == version { isRequesting = false } }
        do {
            try await beforeReadOrEffect(version, scope: scope)
            submissionAttempted = true
            let result: MemoryIntelligenceJob
            switch kind {
            case .prepareModel: result = try await client.prepareModel(scope: scope, requestID: requestID)
            case .retireModelCopy:
                guard let copy = selected.previousCopy else { throw RPCFailure.configuration }
                result = try await client.retireModelCopy(scope: scope, requestID: requestID, copyID: copy.id)
            case .rebuildIndex: result = try await client.rebuild(scope: scope, requestID: requestID, status: status)
            }
            try check(version, scope: scope)
            guard self.status?.policy.digest == selected.policyDigest, self.status?.model.previousCopy == selected.previousCopy else { throw RPCFailure.invalidResponse }
            job = result; pendingJobID = nil; responseUnconfirmed = false
        } catch {
            if epoch == version { report(error, mutation: submissionAttempted); if !responseUnconfirmed { pendingJobID = nil } }
        }
        startPollingIfNeeded()
    }
    public func verifyIndex() async {
        guard let client, let scope, let status, mayReview, status.index.isOpened, job?.settled != false else { return }
        let version = epoch; isRequesting = true; verification = nil; errorMessage = nil
        defer { if epoch == version { isRequesting = false } }
        do {
            try await beforeReadOrEffect(version, scope: scope)
            let result = try await client.verify(scope: scope); try check(version, scope: scope)
            guard self.status?.policy.digest == status.policy.digest, result.generation == status.index.sourceGeneration else { throw RPCFailure.invalidResponse }
            verification = result
        } catch { if epoch == version { report(error, mutation: false) } }
    }
    public func cancelJob() async {
        guard let client, let scope, let before = job, mayCancel else { return }
        let version = epoch; isRequesting = true
        var submissionAttempted = false
        defer { if epoch == version { isRequesting = false } }
        do {
            try await beforeReadOrEffect(version, scope: scope)
            submissionAttempted = true
            let result = try await client.cancel(scope: scope, id: before.id); try check(version, scope: scope)
            guard result.follows(before) else { throw RPCFailure.invalidResponse }; job = result; errorMessage = nil
        } catch { if epoch == version { report(error, mutation: submissionAttempted); if responseUnconfirmed { pendingJobID = before.id } } }
        startPollingIfNeeded()
    }
    public func setVisible(_ id: UUID, visible: Bool) {
        if visible { visibleViews.insert(id); monitoringRequested = true; startPollingIfNeeded() }
        else {
            visibleViews.remove(id)
            if visibleViews.isEmpty {
                if !blocksInteraction { polling?.cancel(); polling = nil; monitoringRequested = false }
                if !isRequesting { review = nil; confirmation = nil }
            }
        }
    }
    private func startPollingIfNeeded() {
        guard monitoringRequested, polling == nil, job?.settled == false || responseUnconfirmed else { return }
        let version = epoch
        pollingPaused = false
        polling = Task { [weak self] in
            guard let self else { return }
            defer { if epoch == version { polling = nil } }
            for _ in 0..<maxPolls {
                do { try await pause(); try Task.checkCancellation() } catch { return }
                guard epoch == version, runtime.isActive else { return }
                await refresh()
                if job?.settled != false && !responseUnconfirmed { return }
            }
            if epoch == version { pollingPaused = true }
        }
    }
}
