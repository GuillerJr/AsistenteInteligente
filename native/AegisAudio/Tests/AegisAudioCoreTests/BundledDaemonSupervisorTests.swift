import Foundation
import Darwin
import Testing
@testable import AegisAudioCore

@Test func bundledDaemonRestartPolicyIsBoundedAndDeterministic() {
    let policy = BundledDaemonRestartPolicy.production

    #expect(policy.delayMilliseconds(afterUnexpectedFailure: 0) == nil)
    #expect(policy.delayMilliseconds(afterUnexpectedFailure: 1) == 250)
    #expect(policy.delayMilliseconds(afterUnexpectedFailure: 2) == 1_000)
    #expect(policy.delayMilliseconds(afterUnexpectedFailure: 3) == 4_000)
    #expect(policy.delayMilliseconds(afterUnexpectedFailure: 4) == nil)
    #expect(policy.stabilityResetMilliseconds == 60_000)
}

@MainActor
private func awaitDaemonCondition(
    _ predicate: () -> Bool,
    timeout: Duration = .seconds(3)
) async throws {
    let clock = ContinuousClock()
    let deadline = clock.now.advanced(by: timeout)
    while !predicate(), clock.now < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(predicate())
}

private func processPlan(executable: URL, directory: URL) -> BundledDaemonLaunchPlan {
    BundledDaemonLaunchPlan(
        executableURL: executable,
        workingDirectoryURL: directory,
        environment: ["PATH": "/usr/bin:/bin"]
    )
}

@Test @MainActor func bundledDaemonStopWaitsAndRejectsOverlappingLaunch() async throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appending(path: "aegis-stop-\(UUID().uuidString)")
    try manager.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: root) }
    let script = root.appending(path: "worker")
    try Data("#!/bin/sh\nexec /bin/sleep 60\n".utf8).write(to: script)
    try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
    let plan = processPlan(executable: script, directory: root)
    let supervisor = BundledDaemonSupervisor(shutdownGraceMilliseconds: 500)
    try supervisor.start(plan: plan)
    let pid = try #require(supervisor.processIdentifier)
    supervisor.stop()
    #expect(supervisor.state == .stopping)
    #expect(throws: BundledDaemonSupervisorError.shutdownInProgress) {
        try supervisor.start(plan: plan)
    }
    async let first: Void = supervisor.stopAndWait()
    async let second: Void = supervisor.stopAndWait()
    _ = await (first, second)
    #expect(supervisor.state == .stopped)
    #expect(supervisor.processIdentifier == nil)
    #expect(Darwin.kill(pid, 0) == -1)
    try supervisor.start(plan: plan)
    #expect(supervisor.state == .running)
    await supervisor.stopAndWait()
}

@Test @MainActor func bundledDaemonStopsChildThatIgnoresTermination() async throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appending(path: "aegis-stuck-\(UUID().uuidString)")
    try manager.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: root) }
    let script = root.appending(path: "worker")
    let ready = root.appending(path: "ready")
    try Data("#!/bin/sh\ntrap '' TERM\ntouch ready\nexec /bin/sleep 60\n".utf8).write(to: script)
    try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
    let supervisor = BundledDaemonSupervisor(shutdownGraceMilliseconds: 100)
    try supervisor.start(plan: processPlan(executable: script, directory: root))
    let pid = try #require(supervisor.processIdentifier)
    try await awaitDaemonCondition { manager.fileExists(atPath: ready.path) }
    let clock = ContinuousClock()
    let started = clock.now
    await supervisor.stopAndWait()
    #expect(started.duration(to: clock.now) < .seconds(2))
    #expect(supervisor.state == .stopped)
    #expect(Darwin.kill(pid, 0) == -1)
}

@Test @MainActor func bundledDaemonCrashLoopExhaustsBudgetAndStaysStopped() async throws {
    let supervisor = BundledDaemonSupervisor(
        restartPolicy: .init(
            retryDelaysMilliseconds: [10, 20, 30],
            stabilityResetMilliseconds: 60_000
        )
    )
    try supervisor.start(plan: processPlan(
        executable: URL(fileURLWithPath: "/usr/bin/false"),
        directory: FileManager.default.temporaryDirectory
    ))
    try await awaitDaemonCondition { supervisor.state == .failed }
    #expect(supervisor.processIdentifier == nil)
    await supervisor.stopAndWait()
    #expect(supervisor.state == .stopped)
}

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
