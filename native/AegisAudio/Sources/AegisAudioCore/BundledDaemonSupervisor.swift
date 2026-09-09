import Foundation
import OSLog

public enum BundledDaemonSupervisorError: Error, Equatable, Sendable {
    case invalidBuildIdentity
    case unsafeExecutable
    case unsafeWorkspace
    case launchFailed
}

public struct BundledDaemonLaunchPlan: Equatable, Sendable {
    public let executableURL: URL
    public let workingDirectoryURL: URL
    public let environment: [String: String]

    public static func resolve(
        bundleURL: URL,
        applicationSupportURL: URL,
        homeDirectoryURL: URL,
        temporaryDirectory: String,
        buildRevision: String,
        fileManager: FileManager = .default
    ) throws -> BundledDaemonLaunchPlan? {
        let executable = bundleURL
            .appendingPathComponent("Contents/Resources/Daemon", isDirectory: true)
            .appendingPathComponent("jarvis-daemon", isDirectory: false)
            .standardizedFileURL
        guard fileManager.fileExists(atPath: executable.path) else {
            return nil
        }
        guard
            buildRevision != JarvisBuildIdentity.development,
            JarvisBuildIdentity.isValid(buildRevision)
        else {
            throw BundledDaemonSupervisorError.invalidBuildIdentity
        }
        guard
            executable.path.hasPrefix(bundleURL.standardizedFileURL.path + "/"),
            !isSymbolicLink(executable, fileManager: fileManager),
            fileManager.isExecutableFile(atPath: executable.path)
        else {
            throw BundledDaemonSupervisorError.unsafeExecutable
        }
        let attributes = try fileManager.attributesOfItem(atPath: executable.path)
        guard
            attributes[.type] as? FileAttributeType == .typeRegular,
            let permissions = attributes[.posixPermissions] as? NSNumber,
            permissions.intValue & 0o022 == 0
        else {
            throw BundledDaemonSupervisorError.unsafeExecutable
        }

        let aegisSupport = applicationSupportURL
            .appendingPathComponent("Aegis", isDirectory: true)
            .standardizedFileURL
        let workspace = aegisSupport.appendingPathComponent("Workspace", isDirectory: true)
        guard workspace.path.hasPrefix(aegisSupport.path + "/") else {
            throw BundledDaemonSupervisorError.unsafeWorkspace
        }
        if fileManager.fileExists(atPath: aegisSupport.path),
           isSymbolicLink(aegisSupport, fileManager: fileManager)
        {
            throw BundledDaemonSupervisorError.unsafeWorkspace
        }
        if fileManager.fileExists(atPath: workspace.path),
           isSymbolicLink(workspace, fileManager: fileManager)
        {
            throw BundledDaemonSupervisorError.unsafeWorkspace
        }
        try fileManager.createDirectory(
            at: workspace,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        guard
            !isSymbolicLink(aegisSupport, fileManager: fileManager),
            !isSymbolicLink(workspace, fileManager: fileManager)
        else {
            throw BundledDaemonSupervisorError.unsafeWorkspace
        }
        try fileManager.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: aegisSupport.path
        )
        try fileManager.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: workspace.path
        )

        let helpers = bundleURL.appendingPathComponent("Contents/Helpers", isDirectory: true)
        var environment = [
            "HOME": homeDirectoryURL.path,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
            "TMPDIR": temporaryDirectory,
            "AEGIS_BUILD_REVISION": buildRevision,
            "AEGIS_WORKSPACE_ROOT": workspace.path,
            "AEGIS_BUNDLED_RUNTIME": "1",
            "AEGIS_LOCAL_BRAIN_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-local-brain").path,
            "AEGIS_LOCAL_EMBEDDING_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-local-embedding").path,
            "AEGIS_MLX_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-mlx-engine").path,
            "AEGIS_MLX_VLM_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-mlx-vlm").path,
            "AEGIS_IOS_BRIDGE_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-ios-bridge").path,
            "AEGIS_BIOMETRIC_TRAINER_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-speaker-trainer").path,
            "AEGIS_BIOMETRIC_CALIBRATOR_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-biometric-calibrator").path,
            "AEGIS_SPOTLIGHT_INDEXER_EXECUTABLE_PATH": helpers
                .appendingPathComponent("jarvis-spotlight-indexer").path,
        ]
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        return BundledDaemonLaunchPlan(
            executableURL: executable,
            workingDirectoryURL: workspace,
            environment: environment
        )
    }

    private static func isSymbolicLink(
        _ url: URL,
        fileManager: FileManager
    ) -> Bool {
        guard let values = try? url.resourceValues(forKeys: [.isSymbolicLinkKey]) else {
            return true
        }
        return values.isSymbolicLink == true
    }
}

public struct BundledDaemonRestartPolicy: Equatable, Sendable {
    public static let production = BundledDaemonRestartPolicy(
        retryDelaysMilliseconds: [250, 1_000, 4_000],
        stabilityResetMilliseconds: 60_000
    )

    public let retryDelaysMilliseconds: [Int64]
    public let stabilityResetMilliseconds: Int64

    public init(
        retryDelaysMilliseconds: [Int64],
        stabilityResetMilliseconds: Int64
    ) {
        precondition(
            !retryDelaysMilliseconds.isEmpty
                && retryDelaysMilliseconds.allSatisfy { $0 > 0 },
            "daemon restart delays must be positive"
        )
        precondition(
            stabilityResetMilliseconds > 0,
            "daemon stability reset must be positive"
        )
        self.retryDelaysMilliseconds = retryDelaysMilliseconds
        self.stabilityResetMilliseconds = stabilityResetMilliseconds
    }

    public func delayMilliseconds(afterUnexpectedFailure failureCount: Int) -> Int64? {
        guard failureCount > 0, failureCount <= retryDelaysMilliseconds.count else {
            return nil
        }
        return retryDelaysMilliseconds[failureCount - 1]
    }
}

@MainActor
public final class BundledDaemonSupervisor {
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "bundled-daemon")
    private let restartPolicy: BundledDaemonRestartPolicy
    private var process: Process?
    private var launchPlan: BundledDaemonLaunchPlan?
    private var restartTask: Task<Void, Never>?
    private var stabilityTask: Task<Void, Never>?
    private var unexpectedFailureCount = 0
    private var launchGeneration = 0
    private var stopRequested = true

    public init(restartPolicy: BundledDaemonRestartPolicy = .production) {
        self.restartPolicy = restartPolicy
    }

    @discardableResult
    public func startIfBundled(
        bundle: Bundle = .main,
        fileManager: FileManager = .default
    ) throws -> Bool {
        if let process, process.isRunning {
            return true
        }
        let bundleURL = bundle.bundleURL
        let applicationSupport = try fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let revision = bundle.object(forInfoDictionaryKey: "AegisBuildRevision") as? String ?? ""
        let plan = try BundledDaemonLaunchPlan.resolve(
            bundleURL: bundleURL,
            applicationSupportURL: applicationSupport,
            homeDirectoryURL: fileManager.homeDirectoryForCurrentUser,
            temporaryDirectory: NSTemporaryDirectory(),
            buildRevision: revision,
            fileManager: fileManager
        )
        guard let plan else {
            logger.debug("No bundled daemon is present; external development daemon remains in use")
            return false
        }
        restartTask?.cancel()
        stabilityTask?.cancel()
        stopRequested = false
        unexpectedFailureCount = 0
        launchPlan = plan
        try launch(plan)
        return true
    }

    public func stop() {
        stopRequested = true
        launchPlan = nil
        restartTask?.cancel()
        restartTask = nil
        stabilityTask?.cancel()
        stabilityTask = nil
        launchGeneration += 1
        guard let process else { return }
        process.terminationHandler = nil
        if process.isRunning {
            process.terminate()
        }
        self.process = nil
    }

    private func launch(_ plan: BundledDaemonLaunchPlan) throws {
        let child = Process()
        child.executableURL = plan.executableURL
        child.currentDirectoryURL = plan.workingDirectoryURL
        child.environment = plan.environment
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        launchGeneration += 1
        let generation = launchGeneration
        child.terminationHandler = { [weak self] terminated in
            let processIdentifier = terminated.processIdentifier
            let terminationStatus = terminated.terminationStatus
            Task { @MainActor [weak self] in
                self?.handleUnexpectedTermination(
                    processIdentifier: processIdentifier,
                    terminationStatus: terminationStatus,
                    generation: generation
                )
            }
        }
        do {
            try child.run()
        } catch {
            child.terminationHandler = nil
            logger.fault("Bundled daemon launch failed")
            throw BundledDaemonSupervisorError.launchFailed
        }
        process = child
        scheduleStabilityReset(for: child.processIdentifier, generation: generation)
        logger.notice(
            "Bundled daemon launched generation=\(generation, privacy: .public)"
        )
    }

    private func handleUnexpectedTermination(
        processIdentifier: Int32,
        terminationStatus: Int32,
        generation: Int
    ) {
        guard
            generation == launchGeneration,
            process?.processIdentifier == processIdentifier
        else {
            return
        }
        process = nil
        stabilityTask?.cancel()
        stabilityTask = nil
        guard !stopRequested else { return }
        unexpectedFailureCount += 1
        logger.error(
            "Bundled daemon exited unexpectedly status=\(terminationStatus, privacy: .public) failure=\(self.unexpectedFailureCount, privacy: .public)"
        )
        scheduleRestart()
    }

    private func scheduleRestart() {
        guard
            !stopRequested,
            let launchPlan,
            let delay = restartPolicy.delayMilliseconds(
                afterUnexpectedFailure: unexpectedFailureCount
            )
        else {
            logger.fault("Bundled daemon restart budget exhausted; manual recovery required")
            return
        }
        restartTask?.cancel()
        restartTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(delay))
            } catch {
                return
            }
            guard let self, !Task.isCancelled, !stopRequested else { return }
            do {
                try launch(launchPlan)
                restartTask = nil
            } catch {
                unexpectedFailureCount += 1
                logger.error(
                    "Bundled daemon restart launch failed failure=\(self.unexpectedFailureCount, privacy: .public)"
                )
                scheduleRestart()
            }
        }
    }

    private func scheduleStabilityReset(for processIdentifier: Int32, generation: Int) {
        stabilityTask?.cancel()
        let delay = restartPolicy.stabilityResetMilliseconds
        stabilityTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(delay))
            } catch {
                return
            }
            guard
                let self,
                !Task.isCancelled,
                generation == launchGeneration,
                process?.processIdentifier == processIdentifier,
                process?.isRunning == true
            else {
                return
            }
            unexpectedFailureCount = 0
            stabilityTask = nil
            logger.notice("Bundled daemon restart budget reset after stable runtime")
        }
    }
}
