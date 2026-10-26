import Foundation
import Observation

@MainActor @Observable public final class RecoveryExtensionStore {
    public private(set) var preview: RecoveryExtensionReview?
    public private(set) var effectReview: RecoveryExtensionReview?
    public private(set) var operation: MaintenanceOperation?
    public private(set) var readOperation: MaintenanceOperation?
    public private(set) var approval: MaintenanceApproval?
    public private(set) var launchPlan: RecoveryLaunchPlan?
    public private(set) var installationNotice: String?
    public private(set) var errorMessage: String?
    public private(set) var isRequesting = false
    public private(set) var isRefreshing = false
    public private(set) var responseUnconfirmed = false
    public private(set) var readAwaitingSettlement = false
    public private(set) var pollingPaused = false
    @ObservationIgnored public var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored private var client: RecoveryExtensionClient?
    @ObservationIgnored private let runtime: ProfileRuntimeGuard
    @ObservationIgnored private let pause: @Sendable () async throws -> Void
    @ObservationIgnored private let maxPolls: Int
    @ObservationIgnored private var polling: Task<Void, Never>?
    private var epoch = 0
    private var readSequence = 0
    private var visibleViews = Set<UUID>()
    private var currentScope: MaintenanceScope?
    private var hostResourcesPending = false
    private struct UnconfirmedRead {
        let kind: String
        let scope: MaintenanceScope
        let previousIDs: Set<String>
    }
    private var unconfirmedRead: UnconfirmedRead?
    private var configuredOnce = false
    public var blocksInteraction: Bool { isRequesting || isRefreshing || preview != nil || responseUnconfirmed || readAwaitingSettlement || hostResourcesPending || operation?.resourcesPending == true || readOperation?.resourcesPending == true }
    public var monitorPaused: Bool { !visibleViews.isEmpty || blocksInteraction }
    public var mayPrepare: Bool { runtime.isActive && !isRequesting && !isRefreshing && !responseUnconfirmed && !readAwaitingSettlement && !hostResourcesPending && operation?.resourcesPending != true && readOperation?.resourcesPending != true }
    public var maySubmit: Bool { mayPrepare && preview.map { $0.expiresAt > Date() && $0.scope == currentScope } == true }
    public var canDecide: Bool {
        guard runtime.isActive, !isRequesting, !responseUnconfirmed, !readAwaitingSettlement, let review = effectReview, let operation, let approval, currentScope == review.scope else { return false }
        return review.matches(approval, operation: operation)
    }
    public var cleanupOperation: MaintenanceOperation? {
        [readOperation, operation].compactMap { $0 }.first { $0.cleanupOwnerPending && !$0.workerPending }
    }
    public init(runtime: ProfileRuntimeGuard, maxPolls: Int = 240, pause: @escaping @Sendable () async throws -> Void = { try await Task.sleep(for: .milliseconds(500)) }) {
        self.runtime = runtime; self.maxPolls = min(240, max(1, maxPolls)); self.pause = pause
    }
    public func configure(_ transport: LocalRPCClient) { configure(RecoveryExtensionClient(transport: transport, runtime: runtime)) }
    public func configure(_ client: RecoveryExtensionClient) {
        guard runtime.isActive, !configuredOnce || !blocksInteraction else { errorMessage = "La recuperación conserva una operación o respuesta pendiente; no se cambiará su conexión."; return }
        epoch += 1; readSequence += 1; polling?.cancel(); self.client = client
        configuredOnce = true
        preview = nil; effectReview = nil; operation = nil; readOperation = nil; approval = nil; launchPlan = nil; installationNotice = nil; errorMessage = nil
        currentScope = nil; unconfirmedRead = nil; hostResourcesPending = false; responseUnconfirmed = false; readAwaitingSettlement = false; isRequesting = false; isRefreshing = false; pollingPaused = false
    }
    public func close() async { epoch += 1; polling?.cancel(); preview = nil; approval = nil; let old = client; client = nil; await old?.close() }
    public func setVisible(_ id: UUID, visible: Bool) { if visible { visibleViews.insert(id) } else { visibleViews.remove(id) }; discardHiddenPreview() }
    public func discardPreview() { guard mayPrepare else { return }; preview = nil }
    private func discardHiddenPreview() { if visibleViews.isEmpty && !isRequesting { preview = nil } }
    private func check(_ version: Int) throws { try runtime.check(); try Task.checkCancellation(); guard epoch == version else { throw CancellationError() } }

    private func runRead(kind: String, _ request: (RecoveryExtensionClient) async throws -> MaintenanceOperation) async throws -> MaintenanceOperation {
        guard let client else { throw RPCFailure.configuration }
        let version = epoch
        try await waitForSupervisionIdle?(); try check(version)
        let before = try await client.status(); try check(version)
        guard !before.closing, !before.operations.contains(where: { $0.resourcesPending }) else { throw RPCFailure.configuration }
        currentScope = before.scope; readOperation = nil; readAwaitingSettlement = true
        unconfirmedRead = UnconfirmedRead(kind: kind, scope: before.scope, previousIDs: Set(before.operations.map(\.id)))
        var row: MaintenanceOperation
        do { row = try await request(client) }
        catch { if !MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error) { readAwaitingSettlement = false; unconfirmedRead = nil }; throw error }
        try check(version)
        guard row.kind == kind, row.scope.profileID == before.scope.profileID else { throw RPCFailure.invalidResponse }
        readOperation = row; unconfirmedRead = nil
        for _ in 0..<maxPolls {
            if !row.resourcesPending || row.cleanupOwnerPending && !row.workerPending { break }
            try await pause(); try check(version)
            let next = try await client.operation(row.id); try check(version)
            guard next.follows(row) else { throw RPCFailure.invalidResponse }; row = next; readOperation = row
        }
        readAwaitingSettlement = row.resourcesPending
        guard !row.resourcesPending else { throw RPCFailure.unavailable }
        readOperation = nil
        guard row.success else { throw RPCFailure.remote(-32040, row.errorCode ?? "recovery_review_failed") }
        let latest = try await client.status(); try check(version)
        guard latest.scope.profileID == before.scope.profileID else { throw RPCFailure.invalidResponse }
        currentScope = latest.scope; hostResourcesPending = latest.operations.contains { $0.resourcesPending }; return row
    }
    private func prepare(kind: String, _ request: (RecoveryExtensionClient) async throws -> MaintenanceOperation, validate: (RecoveryExtensionReview) throws -> Void) async {
        guard mayPrepare else { return }; let version = epoch; isRequesting = true; readSequence += 1; preview = nil
        defer { if epoch == version { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await runRead(kind: kind, request); try check(version)
            try MaintenanceValidation.keys(row.result, ["review"])
            let review = try RecoveryExtensionReview(row.result["review"])
            guard review.scope == currentScope else { throw RPCFailure.invalidResponse }; try validate(review)
            preview = review; errorMessage = nil
        } catch { if epoch == version { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func prepareWorkspaceBackup(entries: [RecoveryWorkspaceEntry], parent: String, name: String) async {
        await prepare(kind: "workspace.backup.review", { try await $0.workspaceBackup(entries: entries, parent: parent, name: name) }) { review in
            guard let detail = review.workspace, detail.isBackup, detail.destinationParent == parent, detail.destinationName == name, detail.selection == entries else { throw RPCFailure.invalidResponse }
        }
    }
    public func prepareWorkspaceRestore(archive: String, hash: String, parent: String, name: String) async {
        await prepare(kind: "workspace.restore.review", { try await $0.workspaceRestore(archive: archive, expectedHash: hash.isEmpty ? nil : hash, parent: parent, name: name) }) { review in
            guard let detail = review.workspace, !detail.isBackup, detail.sourcePath == archive, detail.destinationParent == parent, detail.destinationName == name, hash.isEmpty || detail.summary.manifestSHA == hash else { throw RPCFailure.invalidResponse }
        }
    }
    public func prepareRepair(role: String, kind: String, parent: String, name: String) async {
        await prepare(kind: "doctor.repair.review", { try await $0.repair(role: role, kind: kind, parent: parent, name: name) }) { review in
            guard let detail = review.repair, detail.role == role, detail.repairKind == kind, detail.destinationParent == parent, detail.destinationName == name else { throw RPCFailure.invalidResponse }
        }
    }
    public func prepareRelease(artifact: String, installation: String, verificationID: String) async {
        await prepare(kind: "release.review", { try await $0.release(artifact: artifact, installation: installation, verificationID: verificationID) }) { review in
            guard review.kind == "release.update.apply", review.release?.artifactPath == artifact, review.release?.installRoot == installation else { throw RPCFailure.invalidResponse }
        }
    }
    public func prepareRollback(installation: String, verificationID: String) async {
        await prepare(kind: "release.rollback.review", { try await $0.rollback(installation: installation, verificationID: verificationID) }) { review in
            guard review.kind == "release.rollback.apply", review.release?.installRoot == installation else { throw RPCFailure.invalidResponse }
        }
    }
    public func inspectRelease(installation: String) async {
        guard mayPrepare else { return }; let version = epoch; isRequesting = true; readSequence += 1
        defer { if epoch == version { isRequesting = false } }
        do { let row = try await runRead(kind: "release.inspect") { try await $0.inspect(installation: installation) }; try check(version); installationNotice = try RecoveryInstallationSummary(row.result).notice; errorMessage = nil }
        catch { if epoch == version { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func resolveLaunch(installation: String, verificationID: String, expectedCurrent: String) async {
        guard mayPrepare else { return }; let version = epoch; isRequesting = true; readSequence += 1; launchPlan = nil
        defer { if epoch == version { isRequesting = false } }
        do { let row = try await runRead(kind: "release.resolve_launch") { try await $0.launchPlan(installation: installation, verificationID: verificationID, expectedCurrent: expectedCurrent.isEmpty ? nil : expectedCurrent) }; try check(version); launchPlan = try RecoveryLaunchPlan(row.result); errorMessage = nil }
        catch { if epoch == version { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func submit() async {
        guard let client, let review = preview, maySubmit else { return }; let version = epoch
        isRequesting = true; readSequence += 1; effectReview = review; preview = nil; operation = nil; approval = nil
        defer { if epoch == version { isRequesting = false; discardHiddenPreview() } }
        do { let row = try await client.submit(review); try check(version); operation = row; errorMessage = nil; beginPolling() }
        catch { if epoch == version { responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error); errorMessage = responseUnconfirmed ? "No se confirmó la solicitud. No se reenviará; consulta su estado exacto." : RecoveryExtensionPresentation.error(error) } }
    }
    public func refresh() async {
        guard let client, !isRequesting, !isRefreshing else { return }; let version = epoch, sequence = readSequence; isRefreshing = true
        defer { if epoch == version { isRefreshing = false } }
        func current() throws { try check(version); guard sequence == readSequence, !isRequesting else { throw CancellationError() } }
        do {
            let snapshot = try await client.status(); try current()
            let pending = try await client.pending(); try current()
            guard snapshot.scope == pending.scope else { throw RPCFailure.invalidResponse }
            currentScope = snapshot.scope; hostResourcesPending = snapshot.operations.contains { $0.resourcesPending }
            if let expected = unconfirmedRead, readOperation == nil {
                let candidates = snapshot.operations.filter {
                    !expected.previousIDs.contains($0.id) && $0.kind == expected.kind && $0.scope.profileID == expected.scope.profileID && $0.scope.generation >= expected.scope.generation
                }
                guard candidates.count <= 1 else { throw RPCFailure.invalidResponse }
                if let row = candidates.first {
                    // Adopt custody only, never its review/result or permission.
                    readOperation = row.resourcesPending ? row : nil; readAwaitingSettlement = row.resourcesPending; unconfirmedRead = nil
                } else if !hostResourcesPending { readAwaitingSettlement = false; unconfirmedRead = nil }
            }
            if let old = readOperation {
                if let row = snapshot.operations.first(where: { $0.id == old.id }) {
                    guard row.follows(old) else { throw RPCFailure.invalidResponse }
                    readOperation = row.resourcesPending ? row : nil; readAwaitingSettlement = row.resourcesPending
                } else if old.resourcesPending { readAwaitingSettlement = true }
            } else if readAwaitingSettlement && !hostResourcesPending { readAwaitingSettlement = false }
            var ticket: MaintenanceApproval?
            if let review = effectReview {
                let matches = snapshot.operations.filter { review.matches($0) }
                guard matches.count <= 1 else { throw RPCFailure.invalidResponse }
                if let row = matches.first {
                    if let old = operation { guard row.follows(old) else { throw RPCFailure.invalidResponse } }
                    try review.validateResult(row)
                    operation = row; responseUnconfirmed = false
                } else if operation?.resourcesPending == true { responseUnconfirmed = true }
                if let operation, snapshot.scope == review.scope {
                    let tickets = pending.approvals.filter { review.matches($0, operation: operation) }
                    guard tickets.count <= 1 else { throw RPCFailure.invalidResponse }; ticket = tickets.first
                }
            }
            if let preview, preview.scope != snapshot.scope { self.preview = nil }
            approval = ticket; errorMessage = nil
        } catch { if version == epoch, sequence == readSequence { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func decide(allow: Bool, ticket: MaintenanceApproval) async {
        guard let client, let review = effectReview, let operation, canDecide, approval?.params == ticket.params, approval?.reviewMetadata == ticket.reviewMetadata, review.matches(ticket, operation: operation) else { return }
        let version = epoch; isRequesting = true; readSequence += 1; approval = nil
        defer { if epoch == version { isRequesting = false } }
        do { let accepted = try await client.decide(ticket, review: review, operation: operation, allow: allow); try check(version); errorMessage = accepted ? nil : "La decisión caducó o cambió. No se concedió otra autorización."; beginPolling() }
        catch { if epoch == version { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func cancel() async {
        guard let client, !isRequesting, let captured = readOperation?.resourcesPending == true ? readOperation : operation, !captured.settled else { return }
        let version = epoch; isRequesting = true; readSequence += 1; approval = nil
        defer { if epoch == version { isRequesting = false } }
        do { let row = try await client.cancel(captured); try check(version); if captured.id == readOperation?.id { readOperation = row } else { operation = row }; errorMessage = nil; beginPolling() }
        catch { if epoch == version { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func cleanup() async {
        guard let client, !isRequesting, !isRefreshing, let captured = cleanupOperation else { return }; let version = epoch
        isRequesting = true; readSequence += 1
        defer { if epoch == version { isRequesting = false } }
        do { let row = try await client.cleanup(captured); try check(version); if captured.id == readOperation?.id { readOperation = row.resourcesPending ? row : nil; readAwaitingSettlement = row.resourcesPending } else { operation = row }; hostResourcesPending = row.resourcesPending; errorMessage = nil; beginPolling() }
        catch { if epoch == version { errorMessage = RecoveryExtensionPresentation.error(error) } }
    }
    public func acknowledgeUnconfirmedLocally() {
        guard responseUnconfirmed, operation == nil, !isRequesting, !readAwaitingSettlement, !hostResourcesPending else { return }
        responseUnconfirmed = false; effectReview = nil; approval = nil; errorMessage = "Sólo se descartó la espera local. No acredita rollback ni permite repetir la solicitud anterior."
    }
    public func resumePolling() { beginPolling() }
    private func beginPolling() {
        polling?.cancel(); pollingPaused = false; let version = epoch
        polling = Task { [weak self] in
            guard let self else { return }
            for _ in 0..<self.maxPolls {
                guard !Task.isCancelled, self.epoch == version else { return }
                await self.refresh()
                if self.cleanupOperation != nil || (!self.responseUnconfirmed && !self.readAwaitingSettlement && self.operation?.resourcesPending != true && self.readOperation?.resourcesPending != true) { return }
                if self.errorMessage != nil { self.pollingPaused = true; return }
                do { try await self.pause() } catch { return }
            }
            self.pollingPaused = true
        }
    }
}
