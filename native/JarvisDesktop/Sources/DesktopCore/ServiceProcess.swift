import Foundation
import Darwin

public enum ServiceProcessFailure: Error, Equatable, LocalizedError {
    case cleanupUnconfirmed
    public var errorDescription: String? {
        "El cierre del servicio sigue sin confirmar. Reintenta Salir antes de iniciar otro servicio."
    }
}

public struct ServiceChildExit: Sendable, Equatable {
    public let status: Int32?
    public let wasSignalled: Bool?
}

public struct ServiceStopReport: Sendable, Equatable {
    public let ownerID: UUID
    public let processIdentifier: Int32
    public let exit: ServiceChildExit?
    public let killAccepted: Bool
    public var exitConfirmed: Bool { exit != nil }
    public var graceful: Bool { exit?.status == 0 && exit?.wasSignalled == false }
    public var warning: String? {
        guard exitConfirmed else { return ServiceProcessFailure.cleanupUnconfirmed.localizedDescription }
        guard !graceful else { return nil }
        if exit?.status == nil {
            return "Se observó que el proceso ya no es un hijo pendiente, pero no está disponible su código de salida. La limpieza interna y las acciones pendientes no quedaron confirmadas."
        }
        return "El servicio terminó de forma no habitual. Su limpieza interna y las acciones pendientes no quedaron confirmadas; no se repetirán automáticamente."
    }
}

@MainActor
protocol OwnedServiceChild: AnyObject {
    var isRunning: Bool { get }
    var processIdentifier: Int32 { get }
    var observedExit: ServiceChildExit? { get }
    func signal(_ value: Int32) -> Bool
}

struct ServiceKernelIdentity: Equatable {
    let pid: Int32
    let parent: Int32
    let startSeconds: UInt64
    let startMicroseconds: UInt64
}

enum ServiceKernelWait {
    case owned, exited(Int32), notChild, unknown
}

/// Every signal requires a current kernel child relationship and the original
/// launch identity. Foundation's cached isRunning is never signal authority.
@MainActor
final class ServiceKernelOwner {
    let pid: Int32
    private let expected: ServiceKernelIdentity?
    private let identity: () -> ServiceKernelIdentity?
    private let wait: () -> ServiceKernelWait
    private let reportedExit: () -> ServiceChildExit?
    private let emit: (Int32) -> Bool
    private var terminal: ServiceChildExit?
    private var owned = false

    init(pid: Int32, expected: ServiceKernelIdentity?,
         identity: @escaping () -> ServiceKernelIdentity?,
         wait: @escaping () -> ServiceKernelWait,
         reportedExit: @escaping () -> ServiceChildExit?,
         emit: @escaping (Int32) -> Bool) {
        self.pid = pid; self.expected = expected; self.identity = identity
        self.wait = wait; self.reportedExit = reportedExit; self.emit = emit
    }

    var observedExit: ServiceChildExit? {
        if let terminal { return terminal }
        owned = false
        let current = identity()
        if let expected, let current, expected != current {
            // The PID now describes a different process. Do not reap or signal it.
            terminal = reportedExit() ?? ServiceChildExit(status: nil, wasSignalled: nil)
            return terminal
        }
        switch wait() {
        case .owned:
            owned = expected?.pid == pid && current == expected && expected?.parent == getpid()
        case .exited(let raw):
            let signal = raw & 0x7f
            if signal == 0 {
                terminal = ServiceChildExit(status: (raw >> 8) & 0xff, wasSignalled: false)
            } else if signal != 0x7f {
                terminal = ServiceChildExit(status: signal, wasSignalled: true)
            }
        case .notChild:
            // Another reaper may have collected this exact child. ECHILD gives
            // no exit code, so never manufacture status zero from this branch.
            terminal = reportedExit() ?? ServiceChildExit(status: nil, wasSignalled: nil)
        case .unknown:
            break
        }
        return terminal
    }

    func signal(_ value: Int32) -> Bool {
        guard observedExit == nil, owned, let expected else { return false }
        // Revalidate birth identity immediately before the PID-based syscall.
        guard let current = identity() else { return false }
        guard current == expected else {
            terminal = ServiceChildExit(status: nil, wasSignalled: nil)
            owned = false
            return false
        }
        return emit(value)
    }
}

@MainActor
final class FoundationServiceChild: OwnedServiceChild {
    private let child: Process
    private let kernel: ServiceKernelOwner
    var isRunning: Bool { kernel.observedExit == nil }
    var processIdentifier: Int32 { kernel.pid }
    var observedExit: ServiceChildExit? { kernel.observedExit }
    func signal(_ value: Int32) -> Bool { kernel.signal(value) }

    init(child: Process, kernel: ServiceKernelOwner) { self.child = child; self.kernel = kernel }

    private static func identity(_ pid: Int32) -> ServiceKernelIdentity? {
        var info = proc_bsdinfo()
        let size = MemoryLayout<proc_bsdinfo>.size
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(size)) == size,
              info.pbi_pid == UInt32(pid) else { return nil }
        return ServiceKernelIdentity(pid: Int32(info.pbi_pid), parent: Int32(info.pbi_ppid),
                                     startSeconds: info.pbi_start_tvsec,
                                     startMicroseconds: info.pbi_start_tvusec)
    }

    private static func wait(_ pid: Int32) -> ServiceKernelWait {
        for _ in 0..<16 {
            var status: Int32 = 0
            let result = Darwin.waitpid(pid, &status, WNOHANG)
            if result == pid { return .exited(status) }
            if result == 0 { return .owned }
            if errno == ECHILD { return .notChild }
            if errno != EINTR { return .unknown }
        }
        return .unknown
    }

    convenience init(_ options: LaunchOptions) throws {
        let plan = try ServiceLaunchPlan(options)
        let child = Process()
        child.executableURL = plan.executable
        child.arguments = plan.arguments
        child.currentDirectoryURL = plan.workingDirectory
        child.environment = plan.environment
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
        let pid = child.processIdentifier
        self.init(child: child, emit: { Darwin.kill(pid, $0) == 0 })
    }

    convenience init(child: Process, emit: @escaping (Int32) -> Bool) {
        let pid = child.processIdentifier
        let kernel = ServiceKernelOwner(pid: pid, expected: Self.identity(pid),
                                        identity: { Self.identity(pid) }, wait: { Self.wait(pid) },
                                        reportedExit: {
            guard !child.isRunning else { return nil }
            return ServiceChildExit(status: child.terminationStatus,
                                    wasSignalled: child.terminationReason == .uncaughtSignal)
        }, emit: emit)
        self.init(child: child, kernel: kernel)
    }
}

@MainActor
public final class ServiceProcess {
    private var process: (any OwnedServiceChild)?
    private var ownerID: UUID?
    private var stopTask: Task<ServiceStopReport, Never>?
    private var stopID: UUID?
    private var killAccepted = false
    private let makeChild: @MainActor (LaunchOptions) throws -> any OwnedServiceChild
    private let now: @MainActor () -> TimeInterval
    private let sleep: @MainActor (TimeInterval) async throws -> Void
    private let gracePeriod: TimeInterval
    public private(set) var lastStopReport: ServiceStopReport?
    public private(set) var requiresCleanupConfirmation = false

    public convenience init() {
        self.init(makeChild: { try FoundationServiceChild($0) },
                  now: { ProcessInfo.processInfo.systemUptime },
                  sleep: { try await Task.sleep(for: .seconds($0)) }, gracePeriod: 35)
    }

    init(makeChild: @escaping @MainActor (LaunchOptions) throws -> any OwnedServiceChild,
         now: @escaping @MainActor () -> TimeInterval,
         sleep: @escaping @MainActor (TimeInterval) async throws -> Void,
         gracePeriod: TimeInterval) {
        precondition(gracePeriod.isFinite && gracePeriod > 0 && gracePeriod <= 35)
        self.makeChild = makeChild; self.now = now; self.sleep = sleep
        self.gracePeriod = gracePeriod
    }

    public var isRunning: Bool { process?.isRunning == true }
    public var isStopping: Bool { stopTask != nil }
    public var ownedIdentity: ServiceOwnerIdentity? {
        guard let process, let ownerID else { return nil }
        return ServiceOwnerIdentity(ownerID: ownerID, processIdentifier: process.processIdentifier)
    }

    public func start(_ options: LaunchOptions) throws {
        guard stopTask == nil, !requiresCleanupConfirmation else {
            throw ServiceProcessFailure.cleanupUnconfirmed
        }
        guard process == nil else { return }
        let child = try makeChild(options)
        process = child; ownerID = UUID(); killAccepted = false; lastStopReport = nil
    }

    /// True means the owned child's exit was observed, not that all descendants
    /// or external effects were independently verified. Inspect lastStopReport.
    public func stop() async -> Bool {
        if let stopTask { return await stopTask.value.exitConfirmed }
        guard let child = process, let ownerID else { return lastStopReport?.exitConfirmed ?? true }
        requiresCleanupConfirmation = true
        let identity = UUID()
        stopID = identity
        // This single owned task is not cancelled when an individual waiter is.
        let task = Task { @MainActor in await self.stopOwned(child, ownerID: ownerID) }
        stopTask = task
        let result = await task.value
        if stopID == identity { stopTask = nil; stopID = nil }
        return result.exitConfirmed
    }

    private func stopOwned(_ child: any OwnedServiceChild, ownerID: UUID) async -> ServiceStopReport {
        if child.observedExit == nil {
            _ = child.signal(SIGTERM)
            let deadline = now() + gracePeriod
            while child.observedExit == nil {
                let remaining = deadline - now()
                if remaining <= 0 { break }
                do { try await sleep(min(0.05, remaining)) }
                catch { return finish(child, ownerID: ownerID) }
            }
            if child.observedExit == nil {
                // No extra wait beyond the original 35-second budget. A signal
                // ACK is not exit: retain ownership until a later observation.
                killAccepted = child.signal(SIGKILL) || killAccepted
            }
        }
        return finish(child, ownerID: ownerID)
    }

    private func finish(_ child: any OwnedServiceChild, ownerID: UUID) -> ServiceStopReport {
        let result = ServiceStopReport(ownerID: ownerID, processIdentifier: child.processIdentifier,
                                       exit: child.observedExit, killAccepted: killAccepted)
        guard self.ownerID == ownerID else { return result }
        lastStopReport = result
        if result.exitConfirmed {
            process = nil; self.ownerID = nil; requiresCleanupConfirmation = false
        }
        return result
    }
}
