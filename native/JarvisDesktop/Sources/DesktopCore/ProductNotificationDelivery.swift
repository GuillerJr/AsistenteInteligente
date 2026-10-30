import Foundation
import Observation

@MainActor public protocol ProductNotificationCenter: AnyObject {
    var onActivation: (@MainActor (String) async -> Void)? { get set }
    func requestPermission() async throws -> Bool
    func canDeliver() async -> Bool
    func add(identifier: String, title: String, body: String) async throws
    func remove(identifiers: [String])
}

/// Owner opt-in is distinct from a server claim and from tool authorization.
/// Native add is preceded by exact fresh validation; uncertain adds are never
/// retried. Notification taps can only ask the existing store to navigate.
@MainActor @Observable public final class ProductNotificationDelivery {
    public private(set) var permissionGranted = false
    public private(set) var isRequestingPermission = false
    public private(set) var errorMessage: String?
    @ObservationIgnored private let center: any ProductNotificationCenter
    @ObservationIgnored private var isActive: @MainActor () -> Bool = { false }
    @ObservationIgnored private var onNavigate: @MainActor (ProductNotificationNavigation) async -> Void = { _ in }
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var processing: Set<SessionScope> = []
    @ObservationIgnored private var attempted: Set<String> = []
    @ObservationIgnored private var routes: [String: Route] = [:]
    private struct Route { let store: ProductInteractionStore; let claim: ProductNotificationClaim }
    public init(center: any ProductNotificationCenter) { self.center = center }
    public func configure(isActive: @escaping @MainActor () -> Bool,
                          onNavigate: @escaping @MainActor (ProductNotificationNavigation) async -> Void) {
        close(); self.isActive = isActive; self.onNavigate = onNavigate
        center.onActivation = { [weak self] id in await self?.activate(id) }
    }
    /// Call only from the owner's explicit native button. Polling never calls it.
    public func requestPermission() async {
        guard isActive(), !isRequestingPermission else { return }
        let current = generation; isRequestingPermission = true
        defer { if current == generation { isRequestingPermission = false } }
        do {
            let granted = try await center.requestPermission()
            guard current == generation, isActive(), !Task.isCancelled else { return }
            permissionGranted = granted
            errorMessage = granted ? nil : "macOS no concedió avisos a Jarvis. Puedes revisar su permiso en Ajustes del Sistema."
        } catch {
            if current == generation { errorMessage = "No se pudo consultar el permiso de avisos de macOS." }
        }
    }
    public func process(_ store: ProductInteractionStore) async {
        guard isActive(), let scope = store.scope, !processing.contains(scope),
              let preferences = store.snapshot?.notificationPreferences else { return }
        if !preferences.enabled || preferences.silenced { remove(scope: scope); return }
        let current = generation, identity = store.viewIdentity
        processing.insert(scope)
        defer { processing.remove(scope) }
        let allowed = await center.canDeliver()
        guard current == generation, isActive(), identity == store.viewIdentity, !Task.isCancelled else { return }
        permissionGranted = allowed
        guard allowed else { remove(scope: scope); return }
        let currentRows = store.notifications
        let stale = routes.filter { _, route in
            route.store === store && route.claim.scope == scope && !currentRows.contains { row in
                row.id == route.claim.id && row.isCurrent
                    && ["dispatching", "delivered", "unknown"].contains(row.status)
            }
        }.map(\.key)
        center.remove(identifiers: stale)
        for id in stale { routes.removeValue(forKey: id) }
        for row in currentRows where row.status == "queued" && row.isCurrent {
            let key = "\(scope.temporary):\(scope.sessionID):\(row.scopeRunID):\(row.id):\(row.revision)"
            guard !attempted.contains(key), attempted.count < 4096, routes.count < 128 else { continue }
            attempted.insert(key)
            guard let claim = await store.claimNotification(row), current == generation,
                  isActive(), identity == store.viewIdentity, !Task.isCancelled else { continue }
            let id = "jarvis.product.\(claim.claim)"
            routes[id] = Route(store: store, claim: claim)
            // No await between this validation and native add besides entering
            // the center itself; it receives only bounded generic text.
            guard await store.validateNotification(claim), current == generation,
                  isActive(), identity == store.viewIdentity, !Task.isCancelled,
                  store.snapshot?.notificationPreferences.enabled == true,
                  store.snapshot?.notificationPreferences.silenced == false else {
                center.remove(identifiers: [id]); routes.removeValue(forKey: id); continue
            }
            do {
                try await center.add(identifier: id, title: claim.title, body: claim.body)
                guard current == generation, isActive(), identity == store.viewIdentity,
                      store.snapshot?.notificationPreferences.enabled == true,
                      store.snapshot?.notificationPreferences.silenced == false else {
                    center.remove(identifiers: [id]); routes.removeValue(forKey: id)
                    await store.settleNotification(claim, outcome: "unknown"); continue
                }
                await store.settleNotification(claim, outcome: "delivered")
            } catch {
                center.remove(identifiers: [id]); routes.removeValue(forKey: id)
                await store.settleNotification(claim, outcome: "unknown")
            }
        }
    }
    public func remove(scope: SessionScope) {
        let ids = routes.filter { $0.value.claim.scope == scope }.map(\.key)
        center.remove(identifiers: ids)
        for id in ids { routes.removeValue(forKey: id) }
    }
    public func activate(_ identifier: String) async {
        guard isActive(), let route = routes[identifier] else { return }
        let current = generation
        await route.store.refresh()
        guard current == generation, isActive(),
              route.store.snapshot?.notificationPreferences.enabled == true,
              route.store.snapshot?.notificationPreferences.silenced == false,
              let row = route.store.notifications.first(where: { $0.id == route.claim.id }),
              let navigation = await route.store.resumeNotification(row),
              current == generation, isActive(), !Task.isCancelled else { return }
        center.remove(identifiers: [identifier]); routes.removeValue(forKey: identifier)
        await onNavigate(navigation)
    }
    public func close() {
        generation += 1; center.remove(identifiers: Array(routes.keys))
        center.onActivation = nil; routes = [:]; attempted = []; processing = []
        permissionGranted = false; isRequestingPermission = false
    }
}
