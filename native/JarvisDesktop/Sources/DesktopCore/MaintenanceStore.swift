import Foundation
import Observation

@MainActor @Observable
public final class MaintenanceStore {
    public private(set) var status: MaintenanceStatus?
    public private(set) var review: MaintenanceReview?
    public private(set) var operation: MaintenanceOperation?
    public private(set) var approval: MaintenanceApproval?
    public private(set) var verification: MaintenanceVerification?
    public private(set) var verificationPath: String?
    @ObservationIgnored private var requestedVerificationPath: String?
    public private(set) var doctor: DoctorSummary?
    public private(set) var errorMessage: String?
    public private(set) var isRequesting = false
    public private(set) var isRefreshing = false
    public private(set) var responseUnconfirmed = false
    public private(set) var readAwaitingSettlement = false
    public private(set) var localAcknowledgement: String?
    public private(set) var pollingPaused = false
    @ObservationIgnored private var client: MaintenanceClient?
    @ObservationIgnored public var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored private var context = 0
    @ObservationIgnored private var readSequence = 0
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private let maxPolls: Int
    @ObservationIgnored private let pause: @Sendable () async throws -> Void
    private var visibleViews: Set<UUID> = []
    private var liveResourcesPending: Bool { operation?.resourcesPending == true || status?.operations.contains(where: { $0.resourcesPending }) == true }
    public var monitorPaused: Bool { MaintenanceMonitorPolicy.pauses(visible: !visibleViews.isEmpty, operation: operation, requestInFlight: isRequesting || isRefreshing, unresolvedResponse: responseUnconfirmed || readAwaitingSettlement) || liveResourcesPending || (review != nil && operation == nil) }
    public init(maxPolls: Int = 240, pause: @escaping @Sendable () async throws -> Void = { try await Task.sleep(for: .milliseconds(500)) }) {
        self.maxPolls = max(0, min(maxPolls, 240)); self.pause = pause
    }
    public func setVisible(_ id: UUID, visible: Bool) {
        if visible { visibleViews.insert(id) } else { visibleViews.remove(id) }
        discardHiddenPreview()
    }
    private func discardHiddenPreview() {
        // Closing a read-only preview cannot leave the chat frozen. A submitted
        // job or uncertain response keeps its owner and reconciliation metadata.
        guard visibleViews.isEmpty, !isRequesting, !responseUnconfirmed, !readAwaitingSettlement,
              operation == nil else { return }
        review = nil; approval = nil
    }
    public var blocksInteraction: Bool { isRequesting || isRefreshing || (review != nil && operation == nil) || responseUnconfirmed || readAwaitingSettlement || liveResourcesPending }
    public var mayPrepare: Bool { !isRequesting && !isRefreshing && !responseUnconfirmed && !readAwaitingSettlement && !liveResourcesPending }
    public var canCleanup: Bool { !isRequesting && !isRefreshing && operation?.cleanupOwnerPending == true && operation?.workerPending == false }

    public func configure(client: LocalRPCClient) {
        configure(client: MaintenanceClient(transport: client))
    }
    public func configure(client: MaintenanceClient) {
        guard self.client == nil || !blocksInteraction else {
            errorMessage = "El mantenimiento aún conserva trabajo o recursos pendientes. No se cambiará su conexión."
            return
        }
        polling?.cancel(); context += 1
        self.client = client
        status = nil; review = nil; operation = nil; approval = nil; verification = nil; verificationPath = nil; requestedVerificationPath = nil; doctor = nil
        responseUnconfirmed = false; readAwaitingSettlement = false; isRequesting = false; isRefreshing = false; readSequence += 1; localAcknowledgement = nil; pollingPaused = false
    }
    public func close() async { polling?.cancel(); context += 1; let client = self.client; self.client = nil; await client?.close() }
    public func discardReview() { guard operation?.resourcesPending != true, !responseUnconfirmed, !readAwaitingSettlement else { return }; review = nil; approval = nil }

    public func refresh() async {
        guard let client, !isRequesting, !isRefreshing else { return }
        let version = context, sequence = readSequence; isRefreshing = true
        defer { if version == context { isRefreshing = false } }
        do {
            let snapshot = try await client.status()
            let pending = try await client.pending()
            guard version == context, sequence == readSequence, !Task.isCancelled else { return }
            guard snapshot.scope == pending.scope else { throw RPCFailure.invalidResponse }
            let profileChanged = status?.scope.profileID != nil && status?.scope.profileID != snapshot.scope.profileID
            status = snapshot
            approval = nil
            if profileChanged {
                verification = nil; verificationPath = nil; requestedVerificationPath = nil; doctor = nil
            }
            if let id = operation?.id {
                if let row = snapshot.operations.first(where: { $0.id == id }) {
                    // A known job cannot silently change its originating scope,
                    // review or kind while retaining an operation ID.
                    guard let previous = operation, row.follows(previous) else { throw RPCFailure.invalidResponse }
                    operation = row; responseUnconfirmed = false
                    if ["doctor.report", "backup.verify"].contains(row.kind), !row.resourcesPending { readAwaitingSettlement = false }
                    try acceptResult(row)
                } else if operation?.resourcesPending == true {
                    // Eviction/restart is uncertainty, never proof of no effect.
                    responseUnconfirmed = true
                }
            }
            if responseUnconfirmed, let review,
               let resolved = try MaintenanceSubmissionReconciliation.find(review, in: snapshot.operations) {
                operation = resolved; responseUnconfirmed = false
                try acceptResult(resolved)
            }
            // Keep an old captured review for reconciliation/receipt display.
            // It cannot approve against a different current generation.
            if let review, review.scope != snapshot.scope, !responseUnconfirmed, operation == nil {
                self.review = nil
            }
            if let review, let operation, operation.phase == "awaiting_owner",
               review.scope == snapshot.scope, operation.scope == snapshot.scope {
                let matches = pending.approvals.filter { $0.matches(review, operation: operation) }
                guard matches.count <= 1 else { throw RPCFailure.invalidResponse }
                approval = matches.first
            }
            errorMessage = nil
        } catch { if version == context, sequence == readSequence { errorMessage = MaintenancePresentation.error(error) } }
    }

    public func acknowledgeUnconfirmedLocally() {
        guard responseUnconfirmed, operation == nil, !isRequesting, !readAwaitingSettlement else { return }
        polling?.cancel()
        responseUnconfirmed = false; review = nil; approval = nil; pollingPaused = false
        localAcknowledgement = MaintenanceSubmissionReconciliation.acknowledgement
        errorMessage = nil
    }

    public func prepareBackup(parent: String, name: String) async {
        await prepare { try await $0.reviewBackup(parent: parent, name: name) }
    }
    public func prepareRestore(parent: String, name: String) async {
        guard let verification else { errorMessage = "Verifica primero la copia que quieres restaurar."; return }
        let identity = verification.id
        await prepare { try await $0.reviewRestore(verificationID: identity, parent: parent, name: name) }
    }
    private func prepare(_ request: (MaintenanceClient) async throws -> MaintenanceReview) async {
        guard let client, mayPrepare else { return }
        isRequesting = true; readSequence += 1; let version = context
        defer { if version == context { isRequesting = false; discardHiddenPreview() } }
        do {
            try await waitForSupervisionIdle?()
            guard version == context, !Task.isCancelled else { throw CancellationError() }
            let snapshot = try await client.status()
            let value = try await request(client)
            guard version == context, !Task.isCancelled else { return }
            guard value.scope == snapshot.scope, !snapshot.closing else { throw RPCFailure.configuration }
            status = snapshot; review = value; operation = nil; approval = nil; doctor = nil; errorMessage = nil; localAcknowledgement = nil
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func submitReview() async {
        guard let client, let review, mayPrepare, review.expiresAt > Date(), review.kind != "backup.create" || !review.sources.contains(where: { $0.disposition == "unavailable" }) else { return }
        isRequesting = true; readSequence += 1; let version = context
        defer { if version == context { isRequesting = false; discardHiddenPreview() } }
        do {
            let value = try await client.submit(review)
            guard version == context else { return }
            guard value.matches(review) else { throw RPCFailure.invalidResponse }
            operation = value; approval = nil; errorMessage = nil; beginPolling()
        } catch {
            if version == context {
                responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
                errorMessage = responseUnconfirmed
                    ? "No se confirmó la solicitud. Actualiza el estado: no se enviará de nuevo automáticamente."
                    : MaintenancePresentation.error(error)
            }
        }
    }
    public func verify(path: String, expectedHash: String) async {
        guard mayPrepare else { return }
        verification = nil; verificationPath = nil; requestedVerificationPath = path
        await startRead { try await $0.verify(path: path, expectedHash: expectedHash.isEmpty ? nil : expectedHash) }
    }
    public func runDoctor() async { await startRead { try await $0.doctor() } }
    private func startRead(_ request: (MaintenanceClient) async throws -> MaintenanceOperation) async {
        guard let client, mayPrepare else { return }
        isRequesting = true; readSequence += 1; let version = context
        defer { if version == context { isRequesting = false; discardHiddenPreview() } }
        review = nil; approval = nil; doctor = nil
        do {
            try await waitForSupervisionIdle?()
            guard version == context, !Task.isCancelled else { throw CancellationError() }
            let snapshot = try await client.status()
            guard version == context, !Task.isCancelled else { throw CancellationError() }
            guard !snapshot.closing, !snapshot.operations.contains(where: { $0.resourcesPending }) else { throw RPCFailure.configuration }
            status = snapshot; operation = nil; readAwaitingSettlement = true
            let value = try await request(client)
            guard version == context else { return }
            guard value.scope.profileID == snapshot.scope.profileID, value.scope.generation >= snapshot.scope.generation else { throw RPCFailure.invalidResponse }
            operation = value; readAwaitingSettlement = value.resourcesPending; errorMessage = nil; beginPolling()
        } catch {
            if version == context {
                if !MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error) { readAwaitingSettlement = false }
                errorMessage = readAwaitingSettlement && operation == nil
                    ? "No se confirmó la lectura. Sus recursos pueden seguir pendientes; no se iniciará otra operación. Consulta el estado o cierra el servicio para asentar sus recursos."
                    : MaintenancePresentation.error(error)
            }
        }
    }
    public func decide(allow: Bool) async {
        guard let client, let captured = approval, let review, let operation, !isRequesting,
              status?.scope == captured.scope, status?.scope == review.scope, status?.scope == operation.scope else { return }
        isRequesting = true; readSequence += 1; approval = nil; let version = context
        defer { if version == context { isRequesting = false; discardHiddenPreview() } }
        do {
            let accepted = try await client.decide(captured, review: review, operation: operation, allow: allow)
            guard version == context else { return }
            errorMessage = accepted ? nil : "La autorización caducó o cambió. No se concedió un permiso nuevo."
            beginPolling()
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func cancel() async {
        guard let client, let captured = operation, !isRequesting, !captured.settled else { return }
        isRequesting = true; readSequence += 1; approval = nil; let version = context
        defer { if version == context { isRequesting = false; discardHiddenPreview() } }
        do {
            let value = try await client.cancel(captured.id)
            guard version == context else { return }
            guard value.follows(captured) else { throw RPCFailure.invalidResponse }
            operation = value; errorMessage = nil; beginPolling()
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func cleanup() async {
        guard let client, let captured = operation, canCleanup else { return }
        isRequesting = true; readSequence += 1; let version = context
        defer { if version == context { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await client.cleanup(captured)
            guard version == context else { return }
            operation = row; responseUnconfirmed = false
            if ["doctor.report", "backup.verify"].contains(row.kind), !row.resourcesPending { readAwaitingSettlement = false }
            errorMessage = nil; beginPolling()
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func resumePolling() { guard operation?.resourcesPending == true else { return }; beginPolling() }
    private func beginPolling() {
        polling?.cancel(); pollingPaused = false
        let version = context, polls = maxPolls
        polling = Task { [weak self] in
            let deadline = ContinuousClock.now.advanced(by: .seconds(120))
            for _ in 0..<polls {
                guard !Task.isCancelled, let self, version == self.context else { return }
                await self.refresh()
                guard !Task.isCancelled, version == self.context else { return }
                if self.operation?.resourcesPending == false { return }
                if self.operation?.cleanupOwnerPending == true && self.operation?.workerPending == false { return }
                if self.errorMessage != nil || ContinuousClock.now >= deadline { self.pollingPaused = true; return }
                do { try await self.pause() } catch { return }
            }
            self?.pollingPaused = true
        }
    }
    private func acceptResult(_ operation: MaintenanceOperation) throws {
        guard operation.success else { return }
        if operation.kind == "backup.verify" { verification = try MaintenanceVerification(operation.result); verificationPath = requestedVerificationPath }
        else if operation.kind == "doctor.report" {
            let report = try DoctorSummary(operation.result)
            guard report.scope == status?.scope else { throw RPCFailure.configuration }
            doctor = report
        }
    }
}
