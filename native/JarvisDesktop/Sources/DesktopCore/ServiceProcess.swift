import Foundation

@MainActor
public final class ServiceProcess {
    private var process: Process?

    public init() {}
    public var isRunning: Bool { process?.isRunning == true }

    public func start(_ options: LaunchOptions) throws {
        guard process == nil else { return }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: options.python)
        child.arguments = ["-m", "jarvis_core.service", "--state", options.state,
                           "--codex", options.codex]
        if let providerState = options.providerState { child.arguments! += ["--provider-state", providerState] }
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = options.source
        environment["PYTHONUNBUFFERED"] = "1"
        child.environment = environment
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
        process = child
    }

    public func stop() async -> Bool {
        guard let child = process else { return true }
        if child.isRunning { child.terminate() }
        // Service shutdown owns run cleanup and the official Codex process.
        for _ in 0..<700 {
            if !child.isRunning { break }
            try? await Task.sleep(for: .milliseconds(50))
        }
        let graceful = !child.isRunning
        if !graceful { kill(child.processIdentifier, SIGKILL) }
        process = nil
        return graceful
    }
}
