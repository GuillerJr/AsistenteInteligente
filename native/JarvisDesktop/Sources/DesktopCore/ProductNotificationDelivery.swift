import Foundation
import Observation

@MainActor public protocol ProductNotificationCenter: AnyObject {
    var onActivation: (@MainActor (String) async -> Void)? { get set }
    func requestPermission() async throws -> Bool
    func canDeliver() async -> Bool
    func existingIdentifiers(namespace: String) async -> [String]
    func add(identifier: String, title: String, body: String, namespace: String) async throws
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
    @ObservationIgnored private var profileNamespace: @MainActor () -> String? = { nil }
    @ObservationIgnored private var onNavigate: @MainActor (ProductNotificationNavigation) async -> Void = { _ in }
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var processing: Set<SessionScope> = []
    @ObservationIgnored private var attempted: Set<String> = []
    @ObservationIgnored private var routes: [String: Route] = [:]
    @ObservationIgnored private var reconciledNamespaces: Set<String> = []
    @ObservationIgnored private var reconcilingNamespaces: Set<String> = []
    private struct Route {
        let store: ProductInteractionStore
        let scope: SessionScope
        let recordID: String
        let runID: String
        let namespace: String
    }
    public init(center: any ProductNotificationCenter) { self.center = center }
    public func configure(namespace: @escaping @MainActor () -> String?,
                          isActive: @escaping @MainActor () -> Bool,
                          onNavigate: @escaping @MainActor (ProductNotificationNavigation) async -> Void) {
        close(); profileNamespace = namespace; self.isActive = isActive; self.onNavigate = onNavigate
        center.onActivation = { [weak self] id in await self?.activate(id) }
    }
    /// Old OS requests are removed once per confirmed physical profile. Their
    /// historical outbox rows remain inspectable; they are never republished.
    @discardableResult public func reconcileOwnNotifications() async -> Bool {
        guard isActive(), let namespace = profileNamespace(), ManagementValidation.hash(namespace) else { return false }
        if reconciledNamespaces.contains(namespace) { return true }
        guard !reconcilingNamespaces.contains(namespace) else { return false }
        let current = generation; reconcilingNamespaces.insert(namespace)
        defer { if current == generation { reconcilingNamespaces.remove(namespace) } }
        let existing = await center.existingIdentifiers(namespace: namespace)
        guard current == generation, isActive(), profileNamespace() == namespace, !Task.isCancelled else { return false }
        center.remove(identifiers: existing.filter { $0.hasPrefix("jarvis.product.") })
        reconciledNamespaces.insert(namespace)
        return true
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
              let namespace = profileNamespace(), ManagementValidation.hash(namespace),
              let preferences = store.snapshot?.notificationPreferences else { return }
        let current = generation, identity = store.viewIdentity
        processing.insert(scope)
        defer { processing.remove(scope) }
        guard await reconcileOwnNotifications(), current == generation,
              profileNamespace() == namespace, identity == store.viewIdentity, !Task.isCancelled else { return }
        if !preferences.enabled || preferences.silenced { remove(scope: scope); return }
        let allowed = await center.canDeliver()
        guard current == generation, isActive(), identity == store.viewIdentity, !Task.isCancelled else { return }
        permissionGranted = allowed
        guard allowed else { remove(scope: scope); return }
        let currentRows = store.notifications
        let stale = routes.filter { _, route in
            route.store === store && route.scope == scope && !currentRows.contains { row in
                row.id == route.recordID && row.isCurrent
                    && ["dispatching", "delivered", "unknown"].contains(row.status)
            }
        }.map(\.key)
        center.remove(identifiers: stale)
        for id in stale { routes.removeValue(forKey: id) }
        for row in currentRows where row.isCurrent && ["delivered", "unknown"].contains(row.status) {
            let id = "jarvis.product.\(row.id)"
            if routes[id] != nil || routes.count < 128 {
                // Fresh, admitted metadata restores navigation only. No add,
                // claim nonce reconstruction, grant or automatic model run.
                routes[id] = Route(store: store, scope: scope, recordID: row.id,
                                   runID: row.scopeRunID, namespace: namespace)
            }
        }
        for row in currentRows where row.status == "queued" && row.isCurrent {
            let key = "\(scope.temporary):\(scope.sessionID):\(row.scopeRunID):\(row.id):\(row.revision)"
            guard !attempted.contains(key), attempted.count < 4096, routes.count < 128 else { continue }
            attempted.insert(key)
            guard let claim = await store.claimNotification(row), current == generation,
                  isActive(), identity == store.viewIdentity, !Task.isCancelled else { continue }
            let id = "jarvis.product.\(claim.id)"
            routes[id] = Route(store: store, scope: scope, recordID: claim.id,
                               runID: claim.runID, namespace: namespace)
            // No await between this validation and native add besides entering
            // the center itself; it receives only bounded generic text.
            guard await store.validateNotification(claim), current == generation,
                  profileNamespace() == namespace,
                  isActive(), identity == store.viewIdentity, !Task.isCancelled,
                  store.snapshot?.notificationPreferences.enabled == true,
                  store.snapshot?.notificationPreferences.silenced == false else {
                center.remove(identifiers: [id]); routes.removeValue(forKey: id); continue
            }
            do {
                try await center.add(identifier: id, title: claim.title, body: claim.body, namespace: namespace)
                guard current == generation, isActive(), identity == store.viewIdentity,
                      profileNamespace() == namespace,
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
        let ids = routes.filter { $0.value.scope == scope }.map(\.key)
        center.remove(identifiers: ids)
        for id in ids { routes.removeValue(forKey: id) }
    }
    public func activate(_ identifier: String) async {
        guard isActive(), let route = routes[identifier], profileNamespace() == route.namespace else { return }
        let current = generation
        await route.store.refresh()
        guard current == generation, isActive(),
              profileNamespace() == route.namespace,
              route.store.snapshot?.notificationPreferences.enabled == true,
              route.store.snapshot?.notificationPreferences.silenced == false,
              let row = route.store.notifications.first(where: { $0.id == route.recordID && $0.scopeRunID == route.runID }),
              let navigation = await route.store.resumeNotification(row),
              current == generation, isActive(), !Task.isCancelled else { return }
        center.remove(identifiers: [identifier]); routes.removeValue(forKey: identifier)
        await onNavigate(navigation)
    }
    public func close() {
        generation += 1; center.remove(identifiers: Array(routes.keys))
        center.onActivation = nil; routes = [:]; attempted = []; processing = []
        reconciledNamespaces = []; reconcilingNamespaces = []
        permissionGranted = false; isRequestingPermission = false
    }
}
