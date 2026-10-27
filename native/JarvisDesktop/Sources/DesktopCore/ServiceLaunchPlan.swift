import Foundation

/// Source and locked dependencies are explicit argv paths. No site hooks,
/// .pth files, inherited Python path or implicit venv discovery are executed.
public struct ServiceLaunchPlan: Sendable {
    public let executable: URL
    public let arguments: [String]
    public let environment: [String: String]
    public let workingDirectory = URL(fileURLWithPath: "/", isDirectory: true)

    static let bootstrap = """
    import runpy, sys
    source, dependencies = sys.argv.pop(1), sys.argv.pop(1)
    sys.path.insert(0, source)
    if dependencies:
        sys.path.insert(1, dependencies)
    runpy.run_module('jarvis_core.service', run_name='__main__', alter_sys=True)
    """

    public init(_ options: LaunchOptions, inheritedEnvironment: [String: String] = ProcessInfo.processInfo.environment) throws {
        // Resolve the chosen interpreter itself, without discovering another one.
        // CPython otherwise probes a venv's pyvenv.cfg before the first Python frame.
        let selected = URL(fileURLWithPath: options.python).resolvingSymlinksInPath()
        guard selected.path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: selected.path) else { throw RPCFailure.configuration }
        executable = selected
        arguments = ["-I", "-S", "-B", "-u", "-c", Self.bootstrap, options.source, options.dependencies ?? ""]
            + options.serviceArguments.dropFirst(2)
        let allowed: Set<String> = ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "__CF_USER_TEXT_ENCODING"]
        environment = inheritedEnvironment.filter { allowed.contains($0.key) }
        // -I ignores Python environment tuning, -S skips global/user site hooks,
        // and -B/-u are explicit because isolated mode also ignores their env vars.
    }
}
