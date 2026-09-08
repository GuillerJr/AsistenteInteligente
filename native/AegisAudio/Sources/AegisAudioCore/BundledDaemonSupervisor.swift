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

@MainActor
public final class BundledDaemonSupervisor {
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "bundled-daemon")
    private var process: Process?

    public init() {}

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
        let child = Process()
        child.executableURL = plan.executableURL
        child.currentDirectoryURL = plan.workingDirectoryURL
        child.environment = plan.environment
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        do {
            try child.run()
        } catch {
            logger.fault("Bundled daemon launch failed")
            throw BundledDaemonSupervisorError.launchFailed
        }
        process = child
        logger.notice("Bundled daemon launched")
        return true
    }

    public func stop() {
        guard let process else { return }
        if process.isRunning {
            process.terminate()
        }
        self.process = nil
    }
}
