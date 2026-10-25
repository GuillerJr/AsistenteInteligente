import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class MaintenanceStore {
    var status: MaintenanceStatus?
    var review: MaintenanceReview?
    var operation: MaintenanceOperation?
    var approval: MaintenanceApproval?
    var verification: MaintenanceVerification?
    var verificationPath: String?
    @ObservationIgnored private var requestedVerificationPath: String?
    var doctor: DoctorSummary?
    var errorMessage: String?
    var isRequesting = false
    var responseUnconfirmed = false
    var localAcknowledgement: String?
    var pollingPaused = false
    @ObservationIgnored private var client: MaintenanceClient?
    @ObservationIgnored var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored private var context = 0
    @ObservationIgnored private var polling: Task<Void, Never>?
    private var visibleViews: Set<UUID> = []
    var monitorPaused: Bool { MaintenanceMonitorPolicy.pauses(visible: !visibleViews.isEmpty, operation: operation, requestInFlight: isRequesting, unresolvedResponse: responseUnconfirmed) || (review != nil && operation == nil) }
    func setVisible(_ id: UUID, visible: Bool) {
        if visible { visibleViews.insert(id) } else { visibleViews.remove(id) }
        discardHiddenPreview()
    }
    private func discardHiddenPreview() {
        // Closing a read-only preview cannot leave the chat frozen. A submitted
        // job or uncertain response keeps its owner and reconciliation metadata.
        guard visibleViews.isEmpty, !isRequesting, !responseUnconfirmed,
              operation == nil else { return }
        review = nil; approval = nil
    }
    var blocksInteraction: Bool { isRequesting || (review != nil && operation == nil) || responseUnconfirmed || (operation.map { !$0.settled } ?? false) }
    var mayPrepare: Bool { !isRequesting && !responseUnconfirmed && !(operation.map { !$0.settled } ?? false) }

    func configure(client: LocalRPCClient) {
        polling?.cancel(); context += 1
        self.client = MaintenanceClient(transport: client)
        status = nil; review = nil; operation = nil; approval = nil; verification = nil; verificationPath = nil; requestedVerificationPath = nil; doctor = nil
        responseUnconfirmed = false; localAcknowledgement = nil; pollingPaused = false
    }
    func close() async { polling?.cancel(); context += 1; await client?.close() }
    func discardReview() { guard operation?.settled != false, !responseUnconfirmed else { return }; review = nil; approval = nil }

    func refresh() async {
        guard let client, !isRequesting else { return }
        let version = context
        do {
            let snapshot = try await client.status()
            let pending = try await client.pending()
            guard version == context, !Task.isCancelled else { return }
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
                    operation = row; try acceptResult(row)
                } else {
                    // Eviction/restart is uncertainty, never proof of no effect.
                    operation = nil; responseUnconfirmed = true
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
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }

    func acknowledgeUnconfirmedLocally() {
        guard responseUnconfirmed, operation == nil, !isRequesting else { return }
        polling?.cancel()
        responseUnconfirmed = false; review = nil; approval = nil; pollingPaused = false
        localAcknowledgement = MaintenanceSubmissionReconciliation.acknowledgement
        errorMessage = nil
    }

    func prepareBackup(parent: String, name: String) async {
        await prepare { try await $0.reviewBackup(parent: parent, name: name) }
    }
    func prepareRestore(parent: String, name: String) async {
        guard let verification else { errorMessage = "Verifica primero la copia que quieres restaurar."; return }
        let identity = verification.id
        await prepare { try await $0.reviewRestore(verificationID: identity, parent: parent, name: name) }
    }
    private func prepare(_ request: (MaintenanceClient) async throws -> MaintenanceReview) async {
        guard let client, mayPrepare else { return }
        isRequesting = true; let version = context
        defer { isRequesting = false; discardHiddenPreview() }
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
    func submitReview() async {
        guard let client, let review, mayPrepare, review.expiresAt > Date(), review.kind != "backup.create" || !review.sources.contains(where: { $0.disposition == "unavailable" }) else { return }
        isRequesting = true; let version = context
        defer { isRequesting = false; discardHiddenPreview() }
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
    func verify(path: String, expectedHash: String) async {
        guard mayPrepare else { return }
        verification = nil; verificationPath = nil; requestedVerificationPath = path
        await startRead { try await $0.verify(path: path, expectedHash: expectedHash.isEmpty ? nil : expectedHash) }
    }
    func runDoctor() async { await startRead { try await $0.doctor() } }
    private func startRead(_ request: (MaintenanceClient) async throws -> MaintenanceOperation) async {
        guard let client, mayPrepare else { return }
        isRequesting = true; let version = context
        defer { isRequesting = false; discardHiddenPreview() }
        review = nil; approval = nil; doctor = nil
        do {
            try await waitForSupervisionIdle?()
            guard version == context, !Task.isCancelled else { throw CancellationError() }
            let snapshot = try await client.status()
            let value = try await request(client)
            guard version == context else { return }
            status = snapshot; operation = value; errorMessage = nil; beginPolling()
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    func decide(allow: Bool) async {
        guard let client, let captured = approval, let review, let operation, !isRequesting,
              status?.scope == captured.scope, status?.scope == review.scope, status?.scope == operation.scope else { return }
        isRequesting = true; approval = nil; let version = context
        defer { isRequesting = false; discardHiddenPreview() }
        do {
            let accepted = try await client.decide(captured, review: review, operation: operation, allow: allow)
            guard version == context else { return }
            errorMessage = accepted ? nil : "La autorización caducó o cambió. No se concedió un permiso nuevo."
            beginPolling()
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    func cancel() async {
        guard let client, let id = operation?.id, !isRequesting, operation?.settled == false else { return }
        isRequesting = true; approval = nil; let version = context
        defer { isRequesting = false; discardHiddenPreview() }
        do {
            let value = try await client.cancel(id)
            guard version == context else { return }
            operation = value; errorMessage = nil; beginPolling()
        } catch { if version == context { errorMessage = MaintenancePresentation.error(error) } }
    }
    func resumePolling() { guard operation?.settled == false else { return }; beginPolling() }
    private func beginPolling() {
        polling?.cancel(); pollingPaused = false
        let version = context
        polling = Task { [weak self] in
            let deadline = ContinuousClock.now.advanced(by: .seconds(120))
            for _ in 0..<240 {
                guard !Task.isCancelled, let self, version == self.context else { return }
                await self.refresh()
                guard !Task.isCancelled, version == self.context else { return }
                if self.operation?.settled == true { return }
                if self.errorMessage != nil || ContinuousClock.now >= deadline { self.pollingPaused = true; return }
                do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
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
