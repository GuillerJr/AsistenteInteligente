import Foundation
import Testing
@testable import AegisAudioCore

@Test func bundledDaemonPlanIsSelfContainedAndSanitizesPythonEnvironment() throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appending(
        path: "aegis-bundled-daemon-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? manager.removeItem(at: root) }
    let bundle = root.appending(path: "Jarvis.app", directoryHint: .isDirectory)
    let daemon = bundle.appending(
        path: "Contents/Resources/Daemon/jarvis-daemon",
        directoryHint: .notDirectory
    )
    let support = root.appending(path: "Library/Application Support", directoryHint: .isDirectory)
    try manager.createDirectory(
        at: daemon.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    try Data("arm64-daemon".utf8).write(to: daemon, options: .atomic)
    try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: daemon.path)

    let resolved = try BundledDaemonLaunchPlan.resolve(
        bundleURL: bundle,
        applicationSupportURL: support,
        homeDirectoryURL: root,
        temporaryDirectory: "/private/tmp/",
        buildRevision: String(repeating: "a", count: 40),
        fileManager: manager
    )
    let plan = try #require(resolved)

    #expect(plan.executableURL == daemon.standardizedFileURL)
    #expect(plan.workingDirectoryURL.path.hasSuffix("/Aegis/Workspace"))
    #expect(plan.environment["AEGIS_BUNDLED_RUNTIME"] == "1")
    #expect(plan.environment["AEGIS_WORKSPACE_ROOT"] == plan.workingDirectoryURL.path)
    #expect(
        plan.environment["AEGIS_LOCAL_BRAIN_EXECUTABLE_PATH"]
            == bundle.appending(path: "Contents/Helpers/jarvis-local-brain").path
    )
    #expect(plan.environment["PYTHONPATH"] == nil)
    #expect(plan.environment["PYTHONHOME"] == nil)
}

@Test func bundledDaemonPlanRejectsExecutableSymlink() throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appending(
        path: "aegis-bundled-daemon-link-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? manager.removeItem(at: root) }
    let bundle = root.appending(path: "Jarvis.app", directoryHint: .isDirectory)
    let daemonDirectory = bundle.appending(
        path: "Contents/Resources/Daemon",
        directoryHint: .isDirectory
    )
    let external = root.appending(path: "external-daemon", directoryHint: .notDirectory)
    try manager.createDirectory(at: daemonDirectory, withIntermediateDirectories: true)
    try Data("external".utf8).write(to: external, options: .atomic)
    try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: external.path)
    try manager.createSymbolicLink(
        at: daemonDirectory.appending(path: "jarvis-daemon"),
        withDestinationURL: external
    )

    #expect(throws: BundledDaemonSupervisorError.unsafeExecutable) {
        try BundledDaemonLaunchPlan.resolve(
            bundleURL: bundle,
            applicationSupportURL: root.appending(path: "Support"),
            homeDirectoryURL: root,
            temporaryDirectory: "/private/tmp/",
            buildRevision: String(repeating: "a", count: 40),
            fileManager: manager
        )
    }
}

@Test func bundledDaemonPlanRejectsUnboundBuildIdentity() throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appending(
        path: "aegis-bundled-daemon-revision-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? manager.removeItem(at: root) }
    let bundle = root.appending(path: "Jarvis.app", directoryHint: .isDirectory)
    let daemon = bundle.appending(
        path: "Contents/Resources/Daemon/jarvis-daemon",
        directoryHint: .notDirectory
    )
    try manager.createDirectory(
        at: daemon.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    try Data("arm64-daemon".utf8).write(to: daemon, options: .atomic)
    try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: daemon.path)

    #expect(throws: BundledDaemonSupervisorError.invalidBuildIdentity) {
        try BundledDaemonLaunchPlan.resolve(
            bundleURL: bundle,
            applicationSupportURL: root.appending(path: "Support"),
            homeDirectoryURL: root,
            temporaryDirectory: "/private/tmp/",
            buildRevision: JarvisBuildIdentity.development,
            fileManager: manager
        )
    }
}
