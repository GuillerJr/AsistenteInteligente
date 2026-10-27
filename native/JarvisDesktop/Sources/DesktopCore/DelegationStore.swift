import Foundation
import Observation

@MainActor @Observable public final class DelegationStore {
    public private(set) var scope: SessionScope?
    public private(set) var goal: DelegationSnapshot?
    public private(set) var goals: [DelegationSnapshot] = []
    public private(set) var hasMoreGoals = false
    public private(set) var approvals: [ApprovalOperation] = []
    public private(set) var isChanging = false
    public private(set) var isRefreshing = false
    public private(set) var responseUnconfirmed = false
    public private(set) var errorMessage: String?
    public private(set) var steeringNotice: String?
    public private(set) var pendingRequestID: String?
    @ObservationIgnored private var client: DelegationClient?
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var version = 0
    @ObservationIgnored private var mutation = 0
    public var blocksInteraction: Bool { isChanging || responseUnconfirmed || goal.map { !$0.settled } == true }
    public init() {}
    public func configure(_ transport: LocalRPCClient) { configure(DelegationClient(transport: transport)) }
    public func configure(_ client: DelegationClient) {
        version += 1; polling?.cancel(); self.client = client
        scope = nil; goal = nil; goals = []; hasMoreGoals = false; approvals = []; pendingRequestID = nil
        isChanging = false; isRefreshing = false; responseUnconfirmed = false; errorMessage = nil; steeringNotice = nil
    }
    public func setScope(_ scope: SessionScope?) {
        guard !blocksInteraction, self.scope != scope else { return }
        version += 1; polling?.cancel(); self.scope = scope; isRefreshing = false
        goal = nil; goals = []; hasMoreGoals = false; approvals = []; errorMessage = nil; steeringNotice = nil; pendingRequestID = nil
    }
    public func close() async { version += 1; polling?.cancel(); approvals = []; await client?.close(); client = nil }
    public func start(objective: String, children: [DelegatedTaskSpec], maxSteps: Int, timeout: Int) async {
        guard let client, let scope, !blocksInteraction, !isRefreshing else { return }
        let current = version; mutation += 1; isChanging = true; errorMessage = nil; steeringNotice = nil; goal = nil
        let request = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased(); pendingRequestID = request
        defer { if current == version { isChanging = false } }
        do {
            let value = try await client.start(scope: scope, objective: objective, children: children,
                                               requestID: request, maxSteps: maxSteps, timeout: timeout)
            guard current == version else { return }; goal = value; resumePolling()
        } catch {
            if current == version {
                responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
                errorMessage = MaintenancePresentation.error(error)
                if responseUnconfirmed { resumePolling() }
            }
        }
    }
    public func refresh() async {
        guard let client, let scope, !isChanging, !isRefreshing else { return }
        let current = version, sequence = mutation; isRefreshing = true
        defer { if current == version { isRefreshing = false } }
        do {
            var next: DelegationSnapshot?
            if responseUnconfirmed, let request = pendingRequestID {
                next = try await client.lookup(scope: scope, requestID: request)
            } else if let goal { next = try await client.get(goal) }
            else {
                let page = try await client.page(scope: scope)
                guard current == version, sequence == mutation, !Task.isCancelled else { return }
                goals = page.goals; hasMoreGoals = page.hasMore; next = goals.first
            }
            guard current == version, sequence == mutation, !Task.isCancelled else { return }
            let tickets: [ApprovalOperation]
            if let next { tickets = try await client.approvals(next) } else { tickets = [] }
            guard current == version, sequence == mutation, !Task.isCancelled else { return }
            if let next { goal = next; responseUnconfirmed = false }
            approvals = tickets
            errorMessage = responseUnconfirmed ? "El objetivo sigue sin confirmar. Sólo se consulta su identidad; no se vuelve a iniciar." : nil
        } catch { if current == version, sequence == mutation { approvals = []; errorMessage = MaintenancePresentation.error(error) } }
    }
    public func loadMore() async {
        guard let client, let scope, !isChanging, !isRefreshing, hasMoreGoals else { return }
        let current = version, sequence = mutation; isRefreshing = true
        defer { if current == version { isRefreshing = false } }
        do {
            let page = try await client.page(scope: scope, offset: goals.count)
            guard current == version, sequence == mutation, !Task.isCancelled else { return }
            guard Set((goals + page.goals).map(\.id)).count == goals.count + page.goals.count else { throw RPCFailure.invalidResponse }
            goals += page.goals; hasMoreGoals = page.hasMore
        } catch { if current == version, sequence == mutation { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func reloadCatalogue() async {
        guard let client, let scope, !isChanging, !isRefreshing else { return }
        let current = version, sequence = mutation; isRefreshing = true
        defer { if current == version { isRefreshing = false } }
        do {
            let page = try await client.page(scope: scope)
            guard current == version, sequence == mutation, !Task.isCancelled else { return }
            goals = page.goals; hasMoreGoals = page.hasMore
            if goal == nil { goal = goals.first }
        } catch { if current == version, sequence == mutation { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func observe(scope: SessionScope, parentRunID: String, goalID: String) async {
        guard let client, self.scope == scope, !isChanging, goal == nil || goal?.settled == true else { return }
        let current = version; mutation += 1
        do {
            let value = try await client.get(scope: scope, parentRunID: parentRunID, goalID: goalID)
            guard current == version, self.scope == scope else { return }; goal = value; resumePolling()
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func select(_ value: DelegationSnapshot) async {
        guard !blocksInteraction, value.scope == scope, goals.contains(where: { $0.id == value.id }) else { return }
        mutation += 1; let current = version, selection = mutation; goal = value
        for _ in 0..<3000 {
            guard current == version, selection == mutation, !Task.isCancelled else { return }
            if !isRefreshing { break }
            do { try await Task.sleep(for: .milliseconds(10)) } catch { return }
        }
        guard !isRefreshing else { errorMessage = MaintenancePresentation.error(RPCFailure.unavailable); return }
        await refresh(); if goal?.settled == false { resumePolling() }
    }
    public func cancel() async {
        guard let client, let goal, !goal.settled, !isChanging else { return }
        let current = version; mutation += 1; isChanging = true; approvals = []
        do {
            _ = try await client.cancel(goal)
            guard current == version else { return }; errorMessage = nil
        } catch { if current == version { errorMessage = "No se confirmó la cancelación. Consulta el objetivo; la limpieza puede seguir pendiente." } }
        if current == version { isChanging = false; await refresh(); resumePolling() }
    }
    public func steer(child: DelegatedChildSnapshot, message: String) async {
        guard let client, let goal, !isChanging, !goal.settled, goal.children.contains(child) else { return }
        let current = version; mutation += 1; isChanging = true
        let request = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        do {
            let status = try await client.steer(goal, child: child, message: message, requestID: request)
            guard current == version else { return }
            steeringNotice = "Corrección \(request): \(status == "accepted" ? "aceptada" : status == "rejected" ? "rechazada" : "sin confirmar"). No se reenviará automáticamente."
        } catch { if current == version { steeringNotice = "Corrección \(request) sin confirmar. No se reenviará automáticamente." } }
        if current == version { isChanging = false; await refresh() }
    }
    public func decide(_ operation: ApprovalOperation, allow: Bool) async {
        guard let client, let goal, !isChanging, approvals.contains(operation), !allow || operation.expiresAt > Date() else { return }
        let current = version; mutation += 1; isChanging = true; approvals.removeAll(where: { $0.id == operation.id })
        do {
            let accepted = try await client.decide(operation, goal: goal, allow: allow)
            guard current == version else { return }
            errorMessage = accepted ? nil : "La solicitud cambió o caducó. No se concedió un permiso nuevo."
        } catch { if current == version { approvals = []; errorMessage = MaintenancePresentation.error(error) } }
        if current == version { isChanging = false; await refresh() }
    }
    public func resumePolling() {
        polling?.cancel(); let current = version
        polling = Task { [weak self] in
            for _ in 0..<7200 {
                guard let self, current == self.version, !Task.isCancelled else { return }
                await self.refresh()
                if self.goal?.settled == true && !self.responseUnconfirmed { return }
                do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
            }
        }
    }
}
