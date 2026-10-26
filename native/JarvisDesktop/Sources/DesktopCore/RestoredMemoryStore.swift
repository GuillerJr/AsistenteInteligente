import Foundation
import Observation

/// Session-local host metadata. This store owns no model, memory text or grants.
@MainActor @Observable
public final class RestoredMemoryStore {
    public private(set) var agentID = ""
    public private(set) var inventory: RestoredMemoryInventory?
    public private(set) var sources: [RestoredMemorySource] = []
    public private(set) var selectedSourceID: String?
    public private(set) var preview: RestoredMemoryReview?
    public private(set) var effectReview: RestoredMemoryReview?
    public private(set) var operation: MaintenanceOperation?
    public private(set) var readOperation: MaintenanceOperation?
    public private(set) var previousOperation: MaintenanceOperation?
    public private(set) var observation: MemoryCopyObservation?
    public private(set) var observationWasQueried = false
    public private(set) var approval: MaintenanceApproval?
    public private(set) var errorMessage: String?
    public private(set) var isRequesting = false
    public private(set) var isRefreshing = false
    public private(set) var responseUnconfirmed = false
    public private(set) var readAwaitingSettlement = false
    public private(set) var pollingPaused = false
    @ObservationIgnored public var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored private var client: RestoredMemoryClient?
    @ObservationIgnored private let runtime: ProfileRuntimeGuard
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored private var readSequence = 0
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private let pause: @Sendable () async throws -> Void
    @ObservationIgnored private let maxPolls: Int
    private var visibleViews: Set<UUID> = []
    private var currentScope: MaintenanceScope?
    private var inventoryDeadline: Date?
    private var cursors = Set<String>()
    public var blocksInteraction: Bool { isRequesting || isRefreshing || responseUnconfirmed || readAwaitingSettlement || readOperation?.settled == false || operation?.settled == false || operation?.cleanupOwnerPending == true || preview != nil }
    public var monitorPaused: Bool { !visibleViews.isEmpty || blocksInteraction }
    public var mayPrepare: Bool { runtime.isActive && !isRequesting && !isRefreshing && !responseUnconfirmed && !readAwaitingSettlement && readOperation?.settled != false && operation?.settled != false && operation?.cleanupOwnerPending != true }
    public var canDecide: Bool {
        guard runtime.isActive, !isRequesting, !responseUnconfirmed, !readAwaitingSettlement, readOperation?.settled != false,
              let review = effectReview, let operation, let approval, operation.scope == currentScope else { return false }
        return review.matches(approval, operation: operation)
    }
    public var operationNotice: String? {
        if let code = operation?.errorCode, let message = RestoredMemoryPresentation.operationError(code) { return message }
        if let review = effectReview, review.expiresAt <= Date(), operation?.phase == "awaiting_owner" {
            return RestoredMemoryPresentation.operationError("memory_restore_review_expired")
        }
        return nil
    }
    public var selectedSource: RestoredMemorySource? { sources.first { $0.id == selectedSourceID } }
    public var inventoryIsCurrent: Bool { inventoryDeadline.map { $0 > Date() } == true && inventory?.scope.profileID == currentScope?.profileID }
    public var maySubmit: Bool { mayPrepare && preview.map { $0.expiresAt > Date() && $0.scope == currentScope } == true }
    public var hasObservationTarget: Bool { !isRefreshing && runtime.isActive && !readAwaitingSettlement && readOperation?.settled != false && effectReview != nil && operation?.settled == true && operation?.cleanupOwnerPending == false && !isRequesting && !responseUnconfirmed }
    public init(runtime: ProfileRuntimeGuard, maxPolls: Int = 240, pause: @escaping @Sendable () async throws -> Void = { try await Task.sleep(for: .milliseconds(500)) }) {
        self.runtime = runtime; self.maxPolls = min(240, max(1, maxPolls)); self.pause = pause
    }
    public func configure(_ transport: LocalRPCClient) { configure(RestoredMemoryClient(transport: transport, runtime: runtime)) }
    public func configure(_ client: RestoredMemoryClient) {
        epoch += 1; polling?.cancel(); self.client = client
        inventory = nil; sources = []; selectedSourceID = nil; preview = nil; effectReview = nil
        operation = nil; readOperation = nil; previousOperation = nil; approval = nil; observation = nil; observationWasQueried = false
        currentScope = nil; inventoryDeadline = nil; cursors = []; responseUnconfirmed = false; readAwaitingSettlement = false; isRequesting = false; isRefreshing = false; readSequence += 1; pollingPaused = false; errorMessage = nil
    }
    public func close() async { epoch += 1; polling?.cancel(); approval = nil; preview = nil; inventory = nil; sources = []; selectedSourceID = nil; await client?.close() }
    public func setVisible(_ id: UUID, visible: Bool) {
        if visible { visibleViews.insert(id) } else { visibleViews.remove(id) }
        discardHiddenPreview()
    }
    public func selectAgent(_ id: String) {
        guard mayPrepare, id.isEmpty || ManagementValidation.identifier(id, agent: true), id != agentID else { return }
        epoch += 1; polling?.cancel(); agentID = id; clearPreview(); errorMessage = nil
    }
    public func selectSource(_ id: String?) {
        guard mayPrepare, id == nil || sources.contains(where: { $0.id == id }) else { return }
        selectedSourceID = id; preview = nil
    }
    public func discardPreview() { guard mayPrepare else { return }; clearPreview() }
    private func clearPreview() { inventory = nil; inventoryDeadline = nil; sources = []; selectedSourceID = nil; preview = nil; cursors = [] }
    private func discardHiddenPreview() {
        guard visibleViews.isEmpty, !isRequesting else { return }
        clearPreview() // Initiated effect, its review and uncertainty remain owned.
    }
    private func check(_ version: Int) throws { try runtime.check(); try Task.checkCancellation(); guard epoch == version else { throw CancellationError() } }
    private func runRead(_ request: (RestoredMemoryClient) async throws -> MaintenanceOperation) async throws -> MaintenanceOperation {
        guard let client, !readAwaitingSettlement, readOperation?.settled != false else { throw RPCFailure.configuration }
        let version = epoch
        try await waitForSupervisionIdle?(); try check(version)
        let snapshot = try await client.status(); try check(version)
        guard !snapshot.closing else { throw RPCFailure.configuration }; currentScope = snapshot.scope
        readAwaitingSettlement = true
        var row: MaintenanceOperation
        do { row = try await request(client) }
        catch {
            if !MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error) { readAwaitingSettlement = false }
            throw error
        }
        try check(version)
        guard row.scope.profileID == snapshot.scope.profileID else { throw RPCFailure.invalidResponse }; readOperation = row
        for _ in 0..<maxPolls {
            if row.settled { break }
            try await pause(); try check(version)
            let next = try await client.operation(row.id); try check(version)
            guard next.follows(row) else { throw RPCFailure.invalidResponse }; row = next; readOperation = row
        }
        guard row.settled else { throw RPCFailure.unavailable }
        readOperation = nil; readAwaitingSettlement = false
        guard row.success else { throw RPCFailure.remote(-32040, row.errorCode ?? "memory_restore_read_failed") }
        let latest = try await client.status(); try check(version)
        guard latest.scope.profileID == snapshot.scope.profileID else { throw RPCFailure.invalidResponse }; currentScope = latest.scope
        return row
    }
    public func loadInventory() async {
        guard let client, mayPrepare, ManagementValidation.identifier(agentID, agent: true) else { return }
        let version = epoch, selectedAgent = agentID; isRequesting = true
        defer { if version == epoch { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await runRead { _ in try await client.inventory(agentID: selectedAgent) }; try check(version)
            let value = try RestoredMemoryInventory(row.result["inventory"])
            guard value.agentID == selectedAgent, value.scope.profileID == currentScope?.profileID else { throw RPCFailure.invalidResponse }
            inventory = value; inventoryDeadline = value.expiresAt; sources = value.sources; selectedSourceID = nil; preview = nil; cursors = []; errorMessage = nil
            if value.nextCursor == nil, sources.count != value.candidateCount { throw RPCFailure.invalidResponse }
        } catch { if version == epoch { clearPreview(); errorMessage = RestoredMemoryPresentation.error(error) } }
    }
    public func loadMore() async {
        guard let client, let inventory, let cursor = inventory.nextCursor, mayPrepare, inventoryIsCurrent, !cursors.contains(cursor) else { return }
        let version = epoch; isRequesting = true
        defer { if version == epoch { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await runRead { _ in try await client.page(inventoryID: inventory.id, cursor: cursor) }; try check(version)
            let page = try RestoredMemoryInventory(row.result["inventory"])
            guard page.sameSnapshot(as: inventory), page.sources.count > 0, Set(sources.map(\.id)).isDisjoint(with: page.sources.map(\.id)), sources.count + page.sources.count <= inventory.candidateCount, page.nextCursor != cursor else { throw RPCFailure.invalidResponse }
            cursors.insert(cursor)
            if let next = page.nextCursor, cursors.contains(next) { throw RPCFailure.invalidResponse }
            let combined = sources + page.sources
            guard page.nextCursor != nil || combined.count == page.candidateCount else { throw RPCFailure.invalidResponse }
            sources = combined; self.inventory = page; inventoryDeadline = min(inventoryDeadline ?? page.expiresAt, page.expiresAt); errorMessage = nil
        } catch { if version == epoch { clearPreview(); errorMessage = RestoredMemoryPresentation.error(error) } }
    }
    public func prepareReview() async {
        guard let client, let inventory, let source = selectedSource, source.eligible, inventory.destinationEmpty, mayPrepare, inventoryIsCurrent else { return }
        let version = epoch; isRequesting = true
        defer { if version == epoch { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await runRead { _ in try await client.review(inventoryID: inventory.id, sourceHandle: source.id) }; try check(version)
            let value = try RestoredMemoryReview(row.result["review"])
            guard value.matches(inventory, source: source), value.scope == currentScope else { throw RPCFailure.invalidResponse }
            preview = value; errorMessage = nil
        } catch { if version == epoch { preview = nil; errorMessage = RestoredMemoryPresentation.error(error) } }
    }
    public func submit() async {
        guard let client, let review = preview, maySubmit else { return }
        let version = epoch, oldReview = effectReview; isRequesting = true; effectReview = review; previousOperation = operation; operation = nil; approval = nil; observation = nil; observationWasQueried = false
        defer { if version == epoch { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await client.submit(review); try check(version)
            operation = row; preview = nil; errorMessage = nil; beginPolling()
        } catch {
            if version == epoch {
                responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
                if !responseUnconfirmed { operation = previousOperation; effectReview = oldReview }
                preview = nil
                errorMessage = responseUnconfirmed ? "No se confirmó la solicitud. No se reenviará. Consulta el estado para buscar esta revisión exacta." : RestoredMemoryPresentation.error(error)
            }
        }
    }
    private func sameApproval(_ left: MaintenanceApproval?, _ right: MaintenanceApproval?) -> Bool {
        if left == nil && right == nil { return true }
        guard let left, let right else { return false }
        return left.params == right.params && left.reviewID == right.reviewID && left.reviewDigest == right.reviewDigest && left.reviewMetadata == right.reviewMetadata && left.expiresAt == right.expiresAt
    }
    private func checkRefresh(_ version: Int, sequence: Int) throws {
        try check(version)
        guard sequence == readSequence, !isRequesting else { throw CancellationError() }
    }
    public func refresh() async {
        guard let client, !isRequesting, !isRefreshing else { return }
        let version = epoch, sequence = readSequence; isRefreshing = true
        defer { if version == epoch { isRefreshing = false; discardHiddenPreview() } }
        do {
            let snapshot = try await client.status(); try checkRefresh(version, sequence: sequence)
            let pending = try await client.pending(); try checkRefresh(version, sequence: sequence)
            guard snapshot.scope == pending.scope else { throw RPCFailure.invalidResponse }
            currentScope = snapshot.scope
            var nextApproval: MaintenanceApproval?
            if let readOperation, !readOperation.settled {
                if let row = snapshot.operations.first(where: { $0.id == readOperation.id }) {
                    guard row.follows(readOperation) else { throw RPCFailure.invalidResponse }
                    self.readOperation = row.settled ? nil : row
                    if row.settled { readAwaitingSettlement = false }
                } else if !snapshot.operations.contains(where: { $0.workerPending || !$0.settled }) {
                    self.readOperation = nil; readAwaitingSettlement = false
                }
            }
            if readAwaitingSettlement, readOperation == nil, !snapshot.operations.contains(where: { $0.workerPending || !$0.settled }) { readAwaitingSettlement = false }
            if let review = effectReview {
                let matches = snapshot.operations.filter { review.matches($0) }
                guard matches.count <= 1 else { throw RPCFailure.invalidResponse }
                if let row = matches.first {
                    if let operation, !row.follows(operation) { throw RPCFailure.invalidResponse }
                    operation = row; responseUnconfirmed = false
                } else if operation?.settled == false { responseUnconfirmed = true }
                if let operation, operation.scope == snapshot.scope, review.matches(operation) {
                    let tickets = pending.approvals.filter { review.matches($0, operation: operation) }
                    guard tickets.count <= 1 else { throw RPCFailure.invalidResponse }; nextApproval = tickets.first
                }
            }
            // An unchanged read must not withdraw/recreate a live owner card.
            if !sameApproval(approval, nextApproval) { approval = nextApproval }
            if preview?.scope != nil, preview?.scope != snapshot.scope { preview = nil }
            if let inventory, inventory.scope != snapshot.scope { clearPreview() }
            errorMessage = operationNotice
        } catch {
            if version == epoch, sequence == readSequence, !isRequesting, !Task.isCancelled {
                if approval != nil || operation?.settled == false { responseUnconfirmed = true }
                approval = nil; errorMessage = RestoredMemoryPresentation.error(error)
            }
        }
    }
    /// A click is bound to the displayed ticket. A concurrent status read is
    /// harmless; an older reply cannot restore that ticket after this decision.
    public func decide(allow: Bool, ticket: MaintenanceApproval? = nil, now: Date = Date()) async {
        guard runtime.isActive else { errorMessage = "Este perfil ya no está abierto. La decisión no se envió."; return }
        guard !isRequesting else { errorMessage = "Ya hay una solicitud en curso. Espera su resultado antes de decidir otra vez."; return }
        guard let client, let review = effectReview, let operation, let selected = ticket ?? approval else {
            errorMessage = "Esta autorización ya no está disponible. Actualiza el estado para revisar el resultado."; return
        }
        guard review.expiresAt > now, selected.expiresAt > now else {
            approval = nil; errorMessage = RestoredMemoryPresentation.operationError("memory_restore_review_expired"); return
        }
        guard !responseUnconfirmed, !readAwaitingSettlement, readOperation?.settled != false,
              operation.scope == currentScope, sameApproval(approval, selected), review.matches(selected, operation: operation, now: now) else {
            errorMessage = "La autorización cambió. Actualiza el estado; no se envió otra decisión."; return
        }
        let version = epoch; readSequence += 1; isRequesting = true; approval = nil
        defer { if version == epoch { isRequesting = false } }
        do {
            let accepted = try await client.decide(selected, review: review, operation: operation, allow: allow); try check(version)
            errorMessage = accepted ? nil : "La autorización cambió o caducó y la decisión no fue aceptada. Consulta su estado."
            beginPolling()
        } catch {
            if version == epoch {
                errorMessage = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
                    ? "No se confirmó la recepción de tu decisión. No se reenviará; consulta el estado."
                    : RestoredMemoryPresentation.error(error)
                beginPolling()
            }
        }
    }
    public func cancel() async { await updateOperation(cleanup: false) }
    public func cleanup() async { await updateOperation(cleanup: true) }
    private func updateOperation(cleanup: Bool) async {
        guard let client, let operation, !isRequesting, operation.scope.profileID == currentScope?.profileID,
              cleanup ? operation.cleanupOwnerPending : !operation.settled else { return }
        let version = epoch; readSequence += 1; isRequesting = true; approval = nil
        defer { if version == epoch { isRequesting = false } }
        do {
            let row = try await (cleanup ? client.cleanup(operation.id) : client.cancel(operation.id)); try check(version)
            guard row.follows(operation) else { throw RPCFailure.invalidResponse }; self.operation = row; errorMessage = nil; beginPolling()
        } catch { if version == epoch { errorMessage = RestoredMemoryPresentation.error(error) } }
    }
    public func queryReceipt() async {
        guard let client, let operation, let review = effectReview, hasObservationTarget, review.matches(operation), operation.scope.profileID == currentScope?.profileID else { return }
        let version = epoch; isRequesting = true
        defer { if version == epoch { isRequesting = false; discardHiddenPreview() } }
        do {
            let row = try await runRead { _ in try await client.observation(operationID: operation.id, reviewDigest: review.digest) }; try check(version)
            let value = row.result["observation"] == .null ? nil : try MemoryCopyObservation(row.result)
            guard value == nil || (value?.operationID == operation.id && value?.requestDigest == review.digest) else { throw RPCFailure.invalidResponse }
            observation = value; observationWasQueried = true; errorMessage = nil
        } catch { if version == epoch { errorMessage = RestoredMemoryPresentation.error(error) } }
    }
    public func acknowledgeUnconfirmedLocally() {
        guard responseUnconfirmed, !isRequesting, operation == nil || operation?.settled == true else { return }
        readSequence += 1; responseUnconfirmed = false; preview = nil; approval = nil
        errorMessage = "Se cerró sólo el aviso local. El resultado anterior sigue sin confirmar; no se reintentó ni se declaró ausente la copia."
    }
    public func resumePolling() { beginPolling() }
    private func beginPolling() {
        polling?.cancel(); pollingPaused = false; let version = epoch
        polling = Task { [weak self] in
            guard let self else { return }
            for _ in 0..<self.maxPolls {
                do { try await self.pause(); try self.check(version) } catch { return }
                await self.refresh()
                guard self.epoch == version, self.runtime.isActive, !Task.isCancelled else { return }
                if !self.responseUnconfirmed, self.operation?.settled != false { return }
                if self.errorMessage != nil { self.pollingPaused = true; return }
            }
            self.pollingPaused = true
        }
    }
}

public enum RestoredMemoryPresentation {
    public static func operationError(_ code: String) -> String? {
        if code == "memory_restore_review_expired" { return "La revisión caducó. Prepara una revisión nueva para volver a solicitar la copia." }
        if code == "memory_restore_inventory_stale" { return "La lista de ámbitos cambió o caducó. Vuelve a buscarla antes de preparar otra revisión." }
        return nil
    }
    public static func error(_ error: Error) -> String {
        if error is CancellationError { return "La consulta se interrumpió; no se repetirá ninguna copia." }
        if let failure = error as? RPCFailure, case .remote(_, let code) = failure {
            if let message = operationError(code) { return message }
            if code == "memory_copy_receipt_historical" { return "Ese registro es historia importada; no acredita una copia local ni permite repetirla." }
            if code == "profile_lazy_source_unavailable" { return "El almacén no está disponible para una lectura segura. No se abrió ni se migró automáticamente." }
            return "La revisión cambió, caducó o está bloqueada. Consulta el estado y prepara una revisión nueva explícitamente."
        }
        return "No se pudo confirmar la consulta local. No se repetirá la copia automáticamente."
    }
}
