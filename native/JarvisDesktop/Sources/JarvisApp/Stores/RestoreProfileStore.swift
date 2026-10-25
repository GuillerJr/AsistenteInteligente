import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class RestoreProfileStore {
    var status: MaintenanceStatus?
    var review: RestoreProfileReview?
    var operation: MaintenanceOperation?
    var previousUnconfirmed: MaintenanceOperation?
    var inspection: RestoreProfileInspection?
    var approval: MaintenanceApproval?
    var errorMessage: String?
    var isRequesting = false
    var responseUnconfirmed = false
    var pollingPaused = false
    @ObservationIgnored var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored private var client: RestoreProfileClient?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var requestedPath: String?
    private var visibleViews: Set<UUID> = []
    var monitorPaused: Bool { !visibleViews.isEmpty || blocksInteraction }
    var blocksInteraction: Bool { isRequesting || responseUnconfirmed || operation?.settled == false || operation?.cleanupOwnerPending == true || (review != nil && operation == nil) }
    var mayPrepare: Bool { !isRequesting && !responseUnconfirmed && operation?.settled != false && operation?.cleanupOwnerPending != true }
    var openTarget: ProfileOpenTarget? {
        if let review, let operation, let target = try? ProfileOpenTarget(review: review, operation: operation) { return target }
        if let inspection { return try? ProfileOpenTarget(inspection: inspection) }
        return nil
    }
    func configure(_ transport: LocalRPCClient) {
        generation += 1; polling?.cancel(); client = RestoreProfileClient(transport: transport)
        status = nil; review = nil; operation = nil; previousUnconfirmed = nil; inspection = nil; approval = nil
        responseUnconfirmed = false; pollingPaused = false; errorMessage = nil
    }
    func close() async { generation += 1; polling?.cancel(); await client?.close() }
    func setVisible(_ id: UUID, visible: Bool) {
        if visible { visibleViews.insert(id) } else { visibleViews.remove(id) }
        discardHiddenPreview()
    }
    func discardReview() { guard mayPrepare else { return }; review = nil; inspection = nil; approval = nil }
    private func discardHiddenPreview() {
        guard visibleViews.isEmpty, !isRequesting, !responseUnconfirmed, operation == nil else { return }
        review = nil; inspection = nil; approval = nil
    }
    func refresh() async {
        guard let client, !isRequesting else { return }
        let version = generation
        do {
            let snapshot = try await client.status(); let pending = try await client.pending()
            guard version == generation, !Task.isCancelled, snapshot.scope == pending.scope else { return }
            status = snapshot; approval = nil
            if let current = operation {
                guard let row = snapshot.operations.first(where: { $0.id == current.id }), row.follows(current) else {
                    operation = nil; responseUnconfirmed = true; throw RPCFailure.invalidResponse
                }
                operation = row
            } else if responseUnconfirmed, let review {
                let matches = snapshot.operations.filter { review.matches($0) }
                guard matches.count <= 1 else { throw RPCFailure.invalidResponse }
                if let row = matches.first { operation = row; responseUnconfirmed = false }
            }
            if let review, let operation, review.scope == snapshot.scope, operation.phase == "awaiting_owner" {
                let matches = pending.approvals.filter { review.matches($0, operation: operation) }
                guard matches.count <= 1 else { throw RPCFailure.invalidResponse }; approval = matches.first
            }
            try acceptReadResult()
            discardHiddenPreview()
            errorMessage = nil
        } catch { if version == generation { errorMessage = MaintenancePresentation.error(error) } }
    }
    func review(path: String) async { await beginRead(path: path) { try await $0.review(path: path) } }
    func inspect(path: String) async {
        if let operation, operation.phase == "publication_unknown" || operation.phase == "cleanup_pending" { previousUnconfirmed = operation }
        await beginRead(path: path) { try await $0.inspect(path: path) }
    }
    private func beginRead(path: String, request: (RestoreProfileClient) async throws -> MaintenanceOperation) async {
        guard let client, mayPrepare, MaintenanceValidation.path(path) else { return }
        let version = generation; isRequesting = true
        defer { isRequesting = false; discardHiddenPreview() }
        do {
            try await waitForSupervisionIdle?()
            guard version == generation, !Task.isCancelled else { return }
            let snapshot = try await client.status()
            let row = try await request(client)
            guard version == generation, !Task.isCancelled else { return }
            status = snapshot; requestedPath = path; review = nil; inspection = nil; approval = nil; operation = row; errorMessage = nil
            beginPolling()
        } catch { if version == generation { errorMessage = MaintenancePresentation.error(error) } }
    }
    func submit() async {
        guard let client, let review, mayPrepare, review.expiresAt > Date(), review.scope == status?.scope else { return }
        let version = generation; isRequesting = true
        defer { isRequesting = false; discardHiddenPreview() }
        do {
            let row = try await client.activate(review)
            guard version == generation else { return }
            operation = row; inspection = nil; errorMessage = nil; beginPolling()
        } catch {
            if version == generation {
                responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
                errorMessage = responseUnconfirmed ? "No se confirmó la solicitud. No se repetirá. Consulta el estado para buscar esta revisión exacta." : MaintenancePresentation.error(error)
            }
        }
    }
    func decide(allow: Bool) async {
        guard let client, let review, let operation, let ticket = approval, !isRequesting,
              status?.scope == review.scope, review.matches(ticket, operation: operation) else { return }
        let version = generation; isRequesting = true; approval = nil
        defer { isRequesting = false }
        do {
            let accepted = try await client.decide(ticket, review: review, operation: operation, allow: allow)
            guard version == generation else { return }
            errorMessage = accepted ? nil : "La autorización cambió o caducó. Revisa el estado antes de continuar."
            beginPolling()
        } catch { if version == generation { errorMessage = MaintenancePresentation.error(error) } }
    }
    func cancel() async { await updateOperation(cleanup: false) }
    func cleanup() async { await updateOperation(cleanup: true) }
    private func updateOperation(cleanup: Bool) async {
        guard let client, let operation, !isRequesting,
              cleanup ? operation.cleanupOwnerPending : !operation.settled else { return }
        let version = generation; isRequesting = true; approval = nil
        defer { isRequesting = false }
        do {
            let row = try await (cleanup ? client.cleanup(operation.id) : client.cancel(operation.id))
            guard version == generation else { return }
            guard row.follows(operation) else { throw RPCFailure.invalidResponse }
            self.operation = row; errorMessage = nil; beginPolling()
        } catch { if version == generation { errorMessage = MaintenancePresentation.error(error) } }
    }
    func acknowledgeUnconfirmedLocally() {
        guard responseUnconfirmed, operation == nil, !isRequesting else { return }
        polling?.cancel(); responseUnconfirmed = false; review = nil; approval = nil
        errorMessage = "Se cerró sólo este aviso local. El resultado anterior sigue sin confirmar y no se reintentó."
    }
    func resumePolling() { beginPolling() }
    private func acceptReadResult() throws {
        guard let operation, operation.settled, operation.phase == "complete" else { return }
        if operation.kind == "profile.restore.review" {
            try MaintenanceValidationForRestore.result(operation.result, key: "review")
            let value = try RestoreProfileReview(operation.result["review"])
            guard value.path == requestedPath, value.scope.profileID == status?.scope.profileID else { throw RPCFailure.invalidResponse }
            review = value; self.operation = nil
        } else if operation.kind == "profile.restore.inspect" {
            try MaintenanceValidationForRestore.result(operation.result, key: "admission")
            let value = try RestoreProfileInspection(operation.result["admission"])
            guard value.path == requestedPath else { throw RPCFailure.invalidResponse }
            inspection = value
        }
    }
    private func beginPolling() {
        polling?.cancel(); pollingPaused = false
        let version = generation
        polling = Task { [weak self] in
            for _ in 0..<240 {
                guard !Task.isCancelled, let self, self.generation == version else { return }
                await self.refresh()
                guard self.generation == version, !Task.isCancelled else { return }
                if self.operation == nil || self.operation?.settled == true { return }
                if self.errorMessage != nil { self.pollingPaused = true; return }
                do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
            }
            self?.pollingPaused = true
        }
    }
}

// Public RPCValue access keeps the app from storing or displaying unbounded raw JSON.
private enum MaintenanceValidationForRestore {
    static func result(_ value: RPCValue, key: String) throws {
        guard let fields = value.object, Set(fields.keys) == [key] else { throw RPCFailure.invalidResponse }
    }
}
