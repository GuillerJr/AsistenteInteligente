import AegisAudioCore
import AppKit
import Darwin

@MainActor
enum JarvisTerminalLauncher {
    static func open() async throws {
        let manager = FileManager.default
        let support = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                      appropriateFor: nil, create: true)
        guard let plan = try BundledDaemonLaunchPlan.resolve(
            bundleURL: Bundle.main.bundleURL, applicationSupportURL: support,
            homeDirectoryURL: manager.homeDirectoryForCurrentUser,
            temporaryDirectory: NSTemporaryDirectory(), buildRevision: JarvisBuildIdentity.current()
        ), let terminal = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.Terminal")
        else { throw BundledDaemonSupervisorError.unsafeExecutable }
        let directory = try JarvisTerminalCommand.prepareDirectory(in: plan.workingDirectoryURL)
        let command = directory.appendingPathComponent("Jarvis-\(UUID().uuidString).command")
        let descriptor = Darwin.open(command.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o700)
        guard descriptor >= 0 else { throw BundledDaemonSupervisorError.unsafeWorkspace }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        let script = JarvisTerminalCommand.script(executable: plan.executableURL.path,
                                                  environment: plan.environment)
        try handle.write(contentsOf: Data(script.utf8))
        try handle.close()
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        do {
            _ = try await NSWorkspace.shared.open([command], withApplicationAt: terminal,
                                                  configuration: configuration)
        } catch {
            try? manager.removeItem(at: command)
            throw error
        }
    }
}
