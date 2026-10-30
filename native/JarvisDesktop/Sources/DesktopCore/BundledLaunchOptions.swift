import CryptoKit
import Darwin
import Foundation

public enum LaunchDistributionMode: String, Sendable, Equatable {
    case development, selfContained = "self-contained", qualification = "self-contained-qualification"
}

extension LaunchOptions {
    /// Product bundles never accept development overrides or infer a runtime
    /// from PATH. Qualification has a separate signed, hash-pinned profile only.
    public static func resolve(_ arguments: [String], bundle: Bundle = .main,
                               applicationSupport: URL? = nil) throws -> Self {
        try BundledLaunchContext(bundle).resolve(arguments, applicationSupport: applicationSupport)
    }

    /// Copy only immutable, Sendable public bundle metadata across the actor
    /// boundary. File hashing, signature and profile checks run off MainActor.
    public static func resolveCurrentApplication(_ arguments: [String], bundle: Bundle = .main,
                                                  applicationSupport: URL? = nil) async throws -> Self {
        let context = BundledLaunchContext(bundle)
        let work = Task.detached(priority: .userInitiated) { try context.resolve(arguments, applicationSupport: applicationSupport) }
        return try await withTaskCancellationHandler(operation: { try await work.value }, onCancel: { work.cancel() })
    }

    fileprivate static func resolve(_ arguments: [String], context: BundledLaunchContext,
                                    applicationSupport: URL?) throws -> Self {
        let runtimePresent = context.resourceURL.map {
            FileManager.default.fileExists(atPath: $0.appendingPathComponent("JarvisRuntime").path)
        } ?? false
        return try resolve(arguments, channel: context.channel,
            runtimePresent: runtimePresent, hasManifestPin: context.hasManifestPin,
            runtime: { try BundledRuntime.fromSignedBundle(url: context.bundleURL, resource: context.resourceURL,
                executable: context.executable, pin: context.manifestPin) },
            profile: { mode in
                if mode == .qualification { return try qualificationProfile(context) }
                let support: URL
                if let applicationSupport { support = applicationSupport }
                else { support = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true) }
                return try principalProfile(applicationSupport: support)
            })
    }

    static func resolve(_ arguments: [String], channel: String?, runtimePresent: Bool,
                        hasManifestPin: Bool, runtime: () throws -> BundledRuntime,
                        profile: (LaunchDistributionMode) throws -> (state: String, provider: String?)) throws -> Self {
        let requested = channel.flatMap(LaunchDistributionMode.init(rawValue:))
        guard channel == nil || requested != nil else { throw BundledRuntimeFailure.invalidManifest }
        if requested == .selfContained || requested == .qualification || runtimePresent || hasManifestPin {
            guard requested == .selfContained || requested == .qualification else { throw BundledRuntimeFailure.invalidManifest }
            guard arguments.isEmpty else { throw BundledRuntimeFailure.externalArguments }
            let admitted = try runtime()
            let destination = try profile(requested!)
            return Self(python: admitted.python, source: admitted.source, dependencies: admitted.dependencies,
                        state: destination.state, codex: admitted.codex, providerState: destination.provider,
                        bundledRuntime: admitted, distributionMode: requested!)
        }
        guard !arguments.isEmpty else { throw BundledRuntimeFailure.missing }
        return try parse(arguments)
    }

    static func principalProfile(applicationSupport: URL) throws -> (state: String, provider: String?) {
        guard applicationSupport.isFileURL else { throw BundledRuntimeFailure.insecureTree }
        try BundledRuntimePath.validateDirectory(applicationSupport.path)
        let app = applicationSupport.appendingPathComponent("Jarvis Desktop", isDirectory: true)
        let profiles = app.appendingPathComponent("Perfiles", isDirectory: true)
        let principal = profiles.appendingPathComponent("Principal", isDirectory: true)
        let provider = principal.appendingPathComponent("provider", isDirectory: true)
        for directory in [app, profiles, principal, provider] { try preparePrivateDirectory(directory) }
        return (principal.path, provider.path)
    }

    static func runtimeDirectories(state: String) throws -> (home: String, temporary: String) {
        let root = URL(fileURLWithPath: state, isDirectory: true)
        try BundledRuntimePath.validateDirectory(state)
        var info = stat()
        guard lstat(state, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
              info.st_uid == getuid(), info.st_mode & 0o077 == 0 else { throw BundledRuntimeFailure.insecureTree }
        let home = root.appendingPathComponent("runtime-home", isDirectory: true)
        let temporary = root.appendingPathComponent("tmp", isDirectory: true)
        try preparePrivateDirectory(home); try preparePrivateDirectory(temporary)
        return (home.path, temporary.path)
    }

    private static func qualificationProfile(_ context: BundledLaunchContext) throws -> (state: String, provider: String?) {
        guard context.identifier?.hasPrefix("local.jarvis.qa.") == true,
              let resources = context.resourceURL,
              let expected = context.qualificationPin,
              BundledRuntimeManifest.hex(expected, count: 64) else { throw BundledRuntimeFailure.invalidManifest }
        let path = resources.appendingPathComponent("JarvisQualification.json")
        try BundledRuntimePath.validateFile(path.path)
        let fd = Darwin.open(path.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { throw BundledRuntimeFailure.missing }
        defer { Darwin.close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_nlink == 1,
              info.st_size > 0, info.st_size <= 16_384, info.st_mode & 0o022 == 0 else {
            throw BundledRuntimeFailure.insecureTree
        }
        var data = Data(count: Int(info.st_size))
        let count = data.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
        var after = stat()
        guard count == data.count, fstat(fd, &after) == 0, after.st_dev == info.st_dev,
              after.st_ino == info.st_ino, after.st_size == info.st_size,
              BundledRuntime.digest(data) == expected else { throw BundledRuntimeFailure.changed }
        return try qualificationProfile(data)
    }

    static func qualificationProfile(_ data: Data) throws -> (state: String, provider: String?) {
        guard data.count <= 16_384, let value = try? JSONDecoder().decode(RPCValue.self, from: data),
              let fields = value.object,
              Set(fields.keys) == Set(["format", "state"]) || Set(fields.keys) == Set(["format", "state", "provider_state"]),
              value["format"].string == "jarvis.qualification-profile.v1",
              let state = value["state"].string else { throw BundledRuntimeFailure.invalidManifest }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        guard (try? encoder.encode(value)) == data else { throw BundledRuntimeFailure.invalidManifest }
        try qualificationDirectory(state)
        var provider: String?
        if let raw = fields["provider_state"] {
            guard let path = raw.string else { throw BundledRuntimeFailure.invalidManifest }
            guard path != state else { throw BundledRuntimeFailure.invalidManifest }
            try qualificationDirectory(path); provider = path
        }
        return (state, provider)
    }

    private static func qualificationDirectory(_ path: String) throws {
        guard path.hasPrefix("/private/tmp/"), path.utf8.count <= 4096,
              !path.hasSuffix("/"), !path.contains("\0"), !path.contains("//"),
              !path.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }) else {
            throw BundledRuntimeFailure.insecureTree
        }
        try BundledRuntimePath.validateDirectory(path)
        var current = path
        while current != "/private/tmp" {
            var info = stat()
            guard lstat(current, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
                  info.st_uid == getuid(), info.st_mode & 0o077 == 0 else {
                throw BundledRuntimeFailure.insecureTree
            }
            current = URL(fileURLWithPath: current).deletingLastPathComponent().path
        }
    }

    private static func preparePrivateDirectory(_ url: URL) throws {
        try BundledRuntimePath.validateDirectory(url.deletingLastPathComponent().path)
        if mkdir(url.path, 0o700) != 0, errno != EEXIST { throw BundledRuntimeFailure.insecureTree }
        var info = stat()
        guard lstat(url.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
              info.st_uid == getuid(), info.st_mode & 0o077 == 0 else { throw BundledRuntimeFailure.insecureTree }
        try BundledRuntimePath.validateDirectory(url.path)
    }
}

fileprivate struct BundledLaunchContext: Sendable {
    let bundleURL: URL
    let resourceURL: URL?
    let identifier: String?
    let executable: String?
    let channel: String?
    let hasManifestPin: Bool
    let manifestPin: String?
    let qualificationPin: String?
    init(_ bundle: Bundle) {
        bundleURL = bundle.bundleURL; resourceURL = bundle.resourceURL; identifier = bundle.bundleIdentifier
        executable = bundle.object(forInfoDictionaryKey: "CFBundleExecutable") as? String
        channel = bundle.object(forInfoDictionaryKey: "JarvisDistributionChannel") as? String
        hasManifestPin = bundle.object(forInfoDictionaryKey: "JarvisRuntimeManifestSHA256") != nil
        manifestPin = bundle.object(forInfoDictionaryKey: "JarvisRuntimeManifestSHA256") as? String
        qualificationPin = bundle.object(forInfoDictionaryKey: "JarvisQualificationConfigurationSHA256") as? String
    }
    func resolve(_ arguments: [String], applicationSupport: URL?) throws -> LaunchOptions {
        try LaunchOptions.resolve(arguments, context: self, applicationSupport: applicationSupport)
    }
}
