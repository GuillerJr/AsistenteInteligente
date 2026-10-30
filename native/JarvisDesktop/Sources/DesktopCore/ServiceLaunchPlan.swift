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
        if let runtime = options.bundledRuntime {
            guard options.distributionMode != .development, options.python == runtime.python,
                  options.source == runtime.source, options.dependencies == runtime.dependencies,
                  options.codex == runtime.codex, options.automationTestOrigin == nil else {
                throw BundledRuntimeFailure.externalArguments
            }
            try runtime.revalidate()
        } else if options.distributionMode != .development { throw BundledRuntimeFailure.missing }
        // Resolve the chosen interpreter itself, without discovering another one.
        // CPython otherwise probes a venv's pyvenv.cfg before the first Python frame.
        let chosen = URL(fileURLWithPath: options.python)
        let selected = options.bundledRuntime == nil ? chosen.resolvingSymlinksInPath() : chosen
        guard selected.path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: selected.path) else { throw RPCFailure.configuration }
        executable = selected
        let serviceArguments = Array(options.serviceArguments.dropFirst(2))
        arguments = ["-I", "-S", "-B", "-u", "-c", Self.bootstrap, options.source, options.dependencies ?? ""] + serviceArguments
        let allowed: Set<String> = ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "__CF_USER_TEXT_ENCODING"]
        var admittedEnvironment = inheritedEnvironment.filter { allowed.contains($0.key) }
        if let runtime = options.bundledRuntime {
            let directories = try LaunchOptions.runtimeDirectories(state: options.state)
            admittedEnvironment["HOME"] = directories.home
            admittedEnvironment["TMPDIR"] = directories.temporary + "/"
            admittedEnvironment["PATH"] = URL(fileURLWithPath: runtime.python).deletingLastPathComponent().path + ":"
                + URL(fileURLWithPath: runtime.codex).deletingLastPathComponent().path + ":/usr/bin:/bin:/usr/sbin:/sbin"
            admittedEnvironment["SSL_CERT_FILE"] = runtime.certificateBundle
        }
        environment = admittedEnvironment
        // -I ignores Python environment tuning, -S skips global/user site hooks,
        // and -B/-u are explicit because isolated mode also ignores their env vars.
    }
}
