import Foundation
import Observation

@MainActor @Observable public final class ChannelReconciliationStore {
    public private(set) var review: ChannelReconciliationReview?
    public private(set) var operation: MaintenanceOperation?
    public private(set) var approval: MaintenanceApproval?
    public private(set) var observation: ChannelReconciliationObservation?
    public private(set) var errorMessage: String?
    public private(set) var isRequesting = false
    public private(set) var isRefreshing = false
    public private(set) var responseUnconfirmed = false
    @ObservationIgnored public var waitForSupervisionIdle: (@MainActor () async throws -> Void)?
    @ObservationIgnored private var client: ChannelReconciliationClient?
    @ObservationIgnored private var version = 0
    @ObservationIgnored private var reads = 0
    @ObservationIgnored private var polling: Task<Void, Never>?
    private var visible = Set<UUID>()
    private let maxPolls: Int
    @ObservationIgnored private let pause: @Sendable () async throws -> Void
    public var blocksInteraction: Bool { isRequesting || isRefreshing || responseUnconfirmed || operation?.resourcesPending == true || (review != nil && operation == nil) }
    public var monitorPaused: Bool { !visible.isEmpty || blocksInteraction }
    public var mayPrepare: Bool { !isRequesting && !isRefreshing && !responseUnconfirmed && operation?.resourcesPending != true }
    public var canDecide: Bool { !isRequesting && !responseUnconfirmed && review != nil && operation != nil && approval.map { review!.matches($0, operation: operation!) } == true }
    public init(maxPolls: Int = 240, pause: @escaping @Sendable () async throws -> Void = { try await Task.sleep(for: .milliseconds(500)) }) { self.maxPolls = max(0, min(maxPolls, 240)); self.pause = pause }
    public func configure(_ transport: LocalRPCClient) { configure(ChannelReconciliationClient(transport: transport)) }
    public func configure(_ client: ChannelReconciliationClient) {
        version += 1; reads += 1; polling?.cancel(); self.client = client
        review = nil; operation = nil; approval = nil; observation = nil; responseUnconfirmed = false; isRequesting = false; isRefreshing = false; errorMessage = nil
    }
    public func close() async { version += 1; reads += 1; polling?.cancel(); approval = nil; await client?.close() }
    public func setVisible(_ id: UUID, visible: Bool) {
        if visible { self.visible.insert(id) } else { self.visible.remove(id) }
        if self.visible.isEmpty, mayPrepare, operation == nil { discardReview() }
    }
    public func discardReview() { guard mayPrepare else { return }; reads += 1; review = nil; approval = nil }
    public func prepare(chatID: Int, revision: Int, enabled: Bool) async {
        guard let client, mayPrepare else { return }
        reads += 1; let current = version; isRequesting = true; review = nil; approval = nil
        defer { if current == version { isRequesting = false } }
        do {
            try await waitForSupervisionIdle?()
            guard current == version, !Task.isCancelled else { return }
            let value = try await client.review(chatID: chatID, revision: revision, enabled: enabled)
            guard current == version, !Task.isCancelled else { return }
            review = value; operation = nil; approval = nil; observation = nil; errorMessage = nil
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func submit() async {
        guard let client, let review, mayPrepare, review.expiresAt > Date() else { return }
        reads += 1; let current = version; isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            let value = try await client.submit(review)
            guard current == version else { return }; operation = value; errorMessage = nil; resumePolling()
        } catch {
            if current == version { responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error); errorMessage = MaintenancePresentation.error(error); if responseUnconfirmed { resumePolling() } }
        }
    }
    public func refresh() async {
        guard let client, !isRequesting, !isRefreshing else { return }
        let current = version, sequence = reads; isRefreshing = true
        defer { if current == version { isRefreshing = false } }
        do {
            let status = try await client.status()
            guard current == version, sequence == reads, !isRequesting, !Task.isCancelled else { return }
            let pending = try await client.pending()
            guard current == version, sequence == reads, !isRequesting, !Task.isCancelled else { return }
            guard status.scope == pending.scope else { throw RPCFailure.invalidResponse }
            if let operation {
                guard let row = status.operations.first(where: { $0.id == operation.id }), row.follows(operation), review?.matches(row) == true else { throw RPCFailure.invalidResponse }
                self.operation = row; responseUnconfirmed = false
            } else if responseUnconfirmed, let review {
                let matches = status.operations.filter { review.matches($0) }
                guard matches.count <= 1 else { throw RPCFailure.invalidResponse }
                if let row = matches.first { operation = row; responseUnconfirmed = false }
            }
            var next: MaintenanceApproval?
            if let review, let operation, status.scope == review.scope {
                let tickets = pending.approvals.filter { review.matches($0, operation: operation) }
                guard tickets.count <= 1 else { throw RPCFailure.invalidResponse }; next = tickets.first
            }
            if !OwnerDecisionTicketPolicy.same(approval, next) { approval = next }
            errorMessage = nil
        } catch {
            if current == version, sequence == reads, !isRequesting, !Task.isCancelled {
                if approval != nil || operation?.resourcesPending == true { responseUnconfirmed = true }
                approval = nil; errorMessage = MaintenancePresentation.error(error)
            }
        }
    }
    public func decide(allow: Bool) async {
        guard let client, let review, let operation, let ticket = approval, canDecide else { return }
        reads += 1; let current = version; isRequesting = true; approval = nil
        defer { if current == version { isRequesting = false } }
        do {
            let accepted = try await client.decide(ticket, review: review, operation: operation, allow: allow)
            guard current == version else { return }
            errorMessage = accepted ? nil : "La decisión cambió o caducó. Consulta el estado."; resumePolling()
        } catch { if current == version { responseUnconfirmed = true; errorMessage = MaintenancePresentation.error(error); resumePolling() } }
    }
    public func cancel() async {
        guard let client, let operation, !isRequesting, !operation.settled else { return }
        reads += 1; let current = version; isRequesting = true; approval = nil
        defer { if current == version { isRequesting = false } }
        do {
            let row = try await client.cancel(operation.id)
            guard current == version else { return }; guard row.follows(operation) else { throw RPCFailure.invalidResponse }
            self.operation = row; errorMessage = nil; resumePolling()
        } catch { if current == version { responseUnconfirmed = true; errorMessage = MaintenancePresentation.error(error) } }
    }
    public func inspect(chatID: Int) async {
        guard let client, mayPrepare else { return }
        reads += 1; let current = version; isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            let value = try await client.inspect(chatID: chatID)
            guard current == version else { return }; observation = value; errorMessage = nil
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func cleanup() async {
        guard let client, let operation, operation.cleanupOwnerPending, !isRequesting else { return }
        reads += 1; let current = version; isRequesting = true; approval = nil
        defer { if current == version { isRequesting = false } }
        do {
            let row = try await client.cleanup(operation.id)
            guard current == version else { return }; guard row.follows(operation) else { throw RPCFailure.invalidResponse }
            self.operation = row; responseUnconfirmed = false; errorMessage = nil; resumePolling()
        } catch { if current == version { responseUnconfirmed = true; errorMessage = MaintenancePresentation.error(error) } }
    }
    public func resumePolling() {
        polling?.cancel(); let current = version
        polling = Task { [weak self] in
            for _ in 0..<(self?.maxPolls ?? 0) {
                guard let self, self.version == current, !Task.isCancelled else { return }
                await self.refresh()
                if self.operation?.settled == true && !self.responseUnconfirmed { return }
                do { try await self.pause() } catch { return }
            }
        }
    }
}
