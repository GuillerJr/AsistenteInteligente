import Foundation

public final class UserSessionExecutionGate: @unchecked Sendable {
    public struct Permit: Equatable, Sendable {
        fileprivate let generation: UInt64
    }

    public struct Lease: Equatable, Sendable {
        fileprivate let identifier: UUID
    }

    private struct ActiveExecution {
        let lease: Lease
        let cancellation: @Sendable () -> Void
    }

    private let lock = NSLock()
    private var available: Bool
    private var generation: UInt64 = 0
    private var activeExecution: ActiveExecution?

    public init(available: Bool = true) {
        self.available = available
    }

    public var isAvailable: Bool {
        lock.withLock { available }
    }

    public var permit: Permit? {
        lock.withLock {
            available ? Permit(generation: generation) : nil
        }
    }

    public func isCurrent(_ permit: Permit) -> Bool {
        lock.withLock {
            available && permit.generation == generation
        }
    }

    public func begin(
        for permit: Permit,
        cancellation: @escaping @Sendable () -> Void,
        start: () throws -> Void
    ) rethrows -> Lease? {
        lock.lock()
        defer { lock.unlock() }
        guard
            available,
            permit.generation == generation,
            activeExecution == nil
        else {
            return nil
        }
        let lease = Lease(identifier: UUID())
        activeExecution = ActiveExecution(lease: lease, cancellation: cancellation)
        do {
            try start()
        } catch {
            activeExecution = nil
            throw error
        }
        return lease
    }

    public func finish(_ lease: Lease) {
        lock.withLock {
            guard activeExecution?.lease == lease else { return }
            activeExecution = nil
        }
    }

    public func suspend() {
        let cancellation = lock.withLock {
            guard available else { return Optional<(@Sendable () -> Void)>.none }
            available = false
            generation &+= 1
            return activeExecution?.cancellation
        }
        cancellation?()
    }

    public func resume() {
        lock.withLock {
            available = true
        }
    }
}
