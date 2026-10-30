import Foundation
import Darwin

public enum ServiceProcessFailure: Error, Equatable, LocalizedError {
    case cleanupUnconfirmed
    case recoveryContextChanged
    case recoveryResourcesBusy
    case recoveryThrottled
    public var errorDescription: String? {
        switch self {
        case .cleanupUnconfirmed:
            "El cierre del servicio sigue sin confirmar. Reintenta Salir antes de iniciar otro servicio."
        case .recoveryContextChanged:
            "La identidad del perfil o del arranque cambió. No se reinició el servicio."
        case .recoveryResourcesBusy:
            "Un recurso del servicio anterior conserva la custodia del perfil. Espera a que cierre; no se iniciará otro servicio."
        case .recoveryThrottled:
            "El servicio falló repetidamente. La recuperación automática se detuvo; revisa el diagnóstico antes de reconectar."
        }
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
    private let parentWatch: ServiceParentWatch?
    var isRunning: Bool { observedExit == nil }
    var processIdentifier: Int32 { kernel.pid }
    var observedExit: ServiceChildExit? {
        let result = kernel.observedExit
        if result != nil { parentWatch?.close() }
        return result
    }
    func signal(_ value: Int32) -> Bool { kernel.signal(value) }

    init(child: Process, kernel: ServiceKernelOwner, parentWatch: ServiceParentWatch? = nil) {
        self.child = child; self.kernel = kernel; self.parentWatch = parentWatch
    }

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

    convenience init(_ options: LaunchOptions, preparedPlan: ServiceLaunchPlan? = nil) throws {
        let plan = try preparedPlan ?? ServiceLaunchPlan(options)
        let parentWatch = try ServiceParentWatch()
        let child = Process()
        child.executableURL = plan.executable
        child.arguments = plan.arguments + ["--parent-watch-stdin"]
        child.currentDirectoryURL = plan.workingDirectory
        var environment = plan.environment
        // The launching desktop chooses its own public pin. The private key
        // remains in the client process and is never passed to the service.
        let identity = try RPCClientIdentity.forProfile(options.state, create: true)
        environment["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"] = identity.publicKey
        child.environment = environment
        child.standardInput = parentWatch.pipe
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
        parentWatch.didLaunch()
        let pid = child.processIdentifier
        self.init(child: child, emit: { Darwin.kill(pid, $0) == 0 }, parentWatch: parentWatch)
    }

    convenience init(child: Process, emit: @escaping (Int32) -> Bool, parentWatch: ServiceParentWatch? = nil) {
        let pid = child.processIdentifier
        let kernel = ServiceKernelOwner(pid: pid, expected: Self.identity(pid),
                                        identity: { Self.identity(pid) }, wait: { Self.wait(pid) },
                                        reportedExit: {
            guard !child.isRunning else { return nil }
            return ServiceChildExit(status: child.terminationStatus,
                                    wasSignalled: child.terminationReason == .uncaughtSignal)
        }, emit: emit)
        self.init(child: child, kernel: kernel, parentWatch: parentWatch)
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
    private var makePreparedChild: (@MainActor (LaunchOptions, ServiceLaunchPlan) throws -> any OwnedServiceChild)?
    private var prepareLaunch: @Sendable (LaunchOptions) async throws -> ServiceLaunchPlan = { try ServiceLaunchPlan($0) }
    private var preparation: (id: UUID, task: Task<ServiceLaunchPlan, Error>)?
    private var preparationStop: Task<Bool, Never>?
    private let now: @MainActor () -> TimeInterval
    private let sleep: @MainActor (TimeInterval) async throws -> Void
    private let gracePeriod: TimeInterval
    private var launchOptions: LaunchOptions?
    private var readyProfileBinding: String?
    private var recoveryAttempts: [TimeInterval] = []
    public private(set) var lastCrashRecoveryReport: ServiceStopReport?
    public private(set) var lastStopReport: ServiceStopReport?
    public private(set) var requiresCleanupConfirmation = false

    public convenience init() {
        self.init(makeChild: { try FoundationServiceChild($0) },
                  now: { ProcessInfo.processInfo.systemUptime },
                  sleep: { try await Task.sleep(for: .seconds($0)) }, gracePeriod: 35)
        makePreparedChild = { try FoundationServiceChild($0, preparedPlan: $1) }
    }

    init(makeChild: @escaping @MainActor (LaunchOptions) throws -> any OwnedServiceChild,
         now: @escaping @MainActor () -> TimeInterval,
         sleep: @escaping @MainActor (TimeInterval) async throws -> Void,
         gracePeriod: TimeInterval) {
        precondition(gracePeriod.isFinite && gracePeriod > 0 && gracePeriod <= 35)
        self.makeChild = makeChild; self.now = now; self.sleep = sleep
        self.gracePeriod = gracePeriod
    }

    convenience init(makeChild: @escaping @MainActor (LaunchOptions) throws -> any OwnedServiceChild,
                     makePreparedChild: @escaping @MainActor (LaunchOptions, ServiceLaunchPlan) throws -> any OwnedServiceChild,
                     prepareLaunch: @escaping @Sendable (LaunchOptions) async throws -> ServiceLaunchPlan,
                     now: @escaping @MainActor () -> TimeInterval,
                     sleep: @escaping @MainActor (TimeInterval) async throws -> Void, gracePeriod: TimeInterval) {
        self.init(makeChild: makeChild, now: now, sleep: sleep, gracePeriod: gracePeriod)
        self.makePreparedChild = makePreparedChild; self.prepareLaunch = prepareLaunch
    }

    public var isRunning: Bool { process?.isRunning == true }
    public var isStopping: Bool { stopTask != nil || preparationStop != nil }
    public var ownedIdentity: ServiceOwnerIdentity? {
        guard let process, let ownerID else { return nil }
        return ServiceOwnerIdentity(ownerID: ownerID, processIdentifier: process.processIdentifier)
    }

    public func start(_ options: LaunchOptions) throws {
        guard preparation == nil, preparationStop == nil, stopTask == nil, !requiresCleanupConfirmation else {
            throw ServiceProcessFailure.cleanupUnconfirmed
        }
        guard options.bundledRuntime == nil else { throw BundledRuntimeFailure.invalidManifest }
        guard process == nil else { return }
        let child = try makeChild(options)
        own(child, options: options)
    }

    /// The production path validates and hashes bundled files away from the UI
    /// actor, then creates exactly one child. Cancellation/Stop joins that
    /// validation and invalidates its admission before any launch can occur.
    public func startVerified(_ options: LaunchOptions) async throws {
        guard preparation == nil, preparationStop == nil, stopTask == nil, !requiresCleanupConfirmation else {
            throw ServiceProcessFailure.cleanupUnconfirmed
        }
        guard process == nil else { return }
        guard let makePreparedChild else {
            // The injectable unit-test factory has no bundled-file authority.
            try start(options); return
        }
        let id = UUID()
        let prepare = prepareLaunch
        let work = Task.detached(priority: .userInitiated) { try await prepare(options) }
        preparation = (id, work)
        defer { if preparation?.id == id { preparation = nil } }
        let plan = try await withTaskCancellationHandler(operation: { try await work.value }, onCancel: { work.cancel() })
        guard !Task.isCancelled, preparation?.id == id, preparationStop == nil, stopTask == nil,
              !requiresCleanupConfirmation, process == nil else { throw CancellationError() }
        let child = try makePreparedChild(options, plan)
        own(child, options: options)
    }

    private func own(_ child: any OwnedServiceChild, options: LaunchOptions) {
        process = child; ownerID = UUID(); killAccepted = false; lastStopReport = nil
        launchOptions = options
        readyProfileBinding = nil
    }

    /// Call only after authenticated readiness for this exact owned child.
    public func confirmReadyProfile(_ options: LaunchOptions) throws {
        guard launchOptions == options, process?.isRunning == true,
              !requiresCleanupConfirmation, stopTask == nil else {
            throw ServiceProcessFailure.cleanupUnconfirmed
        }
        let binding = try RPCClientIdentity.profileBinding(options.state)
        if let readyProfileBinding, readyProfileBinding != binding {
            throw ServiceProcessFailure.recoveryContextChanged
        }
        readyProfileBinding = binding
    }

    /// Admit a new launch only after the kernel observed this owned child exit
    /// and the same physical profile's flock is obtainable. Controlled child
    /// adapters inherit that open file description across the host's crash.
    /// This gate does not replay a run, query a provider, or claim old cleanup.
    @discardableResult
    public func prepareCrashRecovery(_ options: LaunchOptions) throws -> ServiceStopReport {
        guard stopTask == nil, !requiresCleanupConfirmation,
              let child = process, let ownerID, child.observedExit != nil else {
            throw ServiceProcessFailure.cleanupUnconfirmed
        }
        guard launchOptions == options, let readyProfileBinding,
              try RPCClientIdentity.profileBinding(options.state) == readyProfileBinding else {
            throw ServiceProcessFailure.recoveryContextChanged
        }
        recoveryAttempts.removeAll(where: { now() - $0 >= 60 })
        guard recoveryAttempts.count < 2 else { throw ServiceProcessFailure.recoveryThrottled }
        let path = options.state + "/profile.lock"
        let descriptor = Darwin.open(path, O_RDWR | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw ServiceProcessFailure.recoveryResourcesBusy }
        defer { Darwin.close(descriptor) }
        var info = stat(), named = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_mode & 0o777 == 0o600, info.st_uid == getuid(), info.st_nlink == 1,
              info.st_size == 0, flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            throw ServiceProcessFailure.recoveryResourcesBusy
        }
        guard lstat(path, &named) == 0, named.st_dev == info.st_dev, named.st_ino == info.st_ino,
              try RPCClientIdentity.profileBinding(options.state) == readyProfileBinding else {
            throw ServiceProcessFailure.recoveryContextChanged
        }
        let report = finish(child, ownerID: ownerID)
        guard report.exitConfirmed else { throw ServiceProcessFailure.cleanupUnconfirmed }
        lastCrashRecoveryReport = report
        recoveryAttempts.append(now())
        return report
    }

    /// True means the owned child's exit was observed, not that all descendants
    /// or external effects were independently verified. Inspect lastStopReport.
    public func stop() async -> Bool {
        if let preparationStop { return await preparationStop.value }
        if let pending = preparation {
            // The task only owns reads and descriptors. It is still joined;
            // declaring Stop while it can later launch would be incorrect.
            let task = Task { @MainActor in
                pending.task.cancel()
                _ = try? await pending.task.value
                if self.preparation?.id == pending.id { self.preparation = nil }
                self.preparationStop = nil
                return true
            }
            preparationStop = task
            return await task.value
        }
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
