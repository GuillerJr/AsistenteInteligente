import Foundation

/// One-way retirement shared by all transports and UI callbacks of one runtime.
/// It is a client lifetime check, never backend authorization.
public final class ProfileRuntimeGuard: @unchecked Sendable {
    public let id = UUID()
    private let lock = NSLock()
    private var retired = false
    public init() {}
    public var isActive: Bool { lock.lock(); defer { lock.unlock() }; return !retired }
    public func retire() { lock.lock(); retired = true; lock.unlock() }
    public func check() throws { guard isActive else { throw CancellationError() } }
    public func check(expectedID: UUID) throws { guard id == expectedID else { throw CancellationError() }; try check() }
    func withActive<T>(_ operation: () throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        guard !retired else { throw CancellationError() }
        return try operation()
    }
}

public struct ServiceOwnerIdentity: Equatable, Sendable {
    public let ownerID: UUID
    public let processIdentifier: Int32
    public init(ownerID: UUID, processIdentifier: Int32) { self.ownerID = ownerID; self.processIdentifier = processIdentifier }
    public func matches(_ report: ServiceStopReport) -> Bool { report.ownerID == ownerID && report.processIdentifier == processIdentifier }
}

/// Bounded RAM archive, keyed by the selected physical origin, never chat ID.
/// A restored profile always receives a new empty SessionDrafts value.
@MainActor
public final class ProfileDraftArchive {
    private var values: [MaintenanceFileIdentity: [SessionDrafts]] = [:]
    public init() {}
    public func archive(_ drafts: SessionDrafts, origin: MaintenanceFileIdentity) throws {
        let snapshot = drafts.durableOnly
        guard snapshot.byteLength > 0 else { return }
        let previous = values.values.flatMap { $0 }
        guard previous.count < 32, previous.reduce(0, { $0 + $1.byteLength }) + snapshot.byteLength <= 16_777_216 else { throw RPCFailure.oversized }
        // Returning explicitly to an already admitted root still starts a fresh
        // draft set. Archiving that later visit must not overwrite an earlier one.
        values[origin, default: []].append(snapshot)
    }
    public func drafts(for origin: MaintenanceFileIdentity) -> SessionDrafts? { values[origin]?.last }
    public func snapshots(for origin: MaintenanceFileIdentity) -> [SessionDrafts] { values[origin] ?? [] }
}

/// Used by the app coordinator itself; tests supply owned synthetic children.
/// A cancelled UI waiter never authorizes a successor or abandons stop ownership.
@MainActor
public final class ProfileHandoffGate {
    public private(set) var inProgress = false
    public init() {}
    public func open(target: ProfileOpenTarget, source: LaunchOptions,
                     origin: MaintenanceFileIdentity, predecessor: ServiceOwnerIdentity,
                     retireAndStop: @MainActor () async -> ServiceStopReport?,
                     start: @MainActor (LaunchOptions) async throws -> Void) async throws {
        guard !inProgress, target.identity != origin else { throw RPCFailure.configuration }
        try Task.checkCancellation()
        inProgress = true
        defer { inProgress = false }
        let report = await retireAndStop()
        guard let report, predecessor.matches(report), report.graceful else { throw ServiceProcessFailure.cleanupUnconfirmed }
        try Task.checkCancellation()
        // Construction drops provider-state. Expectations are constraints which
        // the child must validate before any profile side effect.
        let options = source.openingRestored(target)
        try await start(options)
    }
}
