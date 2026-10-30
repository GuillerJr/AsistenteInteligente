import CryptoKit
import Darwin
import Foundation
import Security

/// A pin describes one admitted physical runtime, not a search path. Replacing
/// the app/runtime requires a fresh launch; recovery cannot adopt another tree.
public struct BundledRuntime: Sendable, Equatable {
    public let root: URL
    public let manifestSHA256: String
    public let sourceCommit: String
    public let python: String
    public let source: String
    public let dependencies: String
    public let codex: String
    public let model: String?
    public var certificateBundle: String { root.appendingPathComponent("certs/cacert.pem").path }
    private let device: UInt64
    private let inode: UInt64
    private var signedBundle: URL?

    public static func resolve(root: URL, manifestSHA256: String) throws -> Self {
        guard root.isFileURL, BundledRuntimeManifest.hex(manifestSHA256, count: 64) else {
            throw BundledRuntimeFailure.invalidManifest
        }
        let tree = try RuntimeFileTree(root)
        let bytes = try tree.read("runtime-manifest.json", limit: BundledRuntimeManifest.maximumManifestBytes)
        guard digest(bytes) == manifestSHA256 else { throw BundledRuntimeFailure.changed }
        let manifest = try BundledRuntimeManifest(bytes)
        try tree.validate(manifest)
        func path(_ key: String) -> String { root.appendingPathComponent(manifest.entrypoints[key]!).path }
        return Self(root: root, manifestSHA256: manifestSHA256, sourceCommit: manifest.sourceCommit,
                    python: path("python"), source: path("source"), dependencies: path("dependencies"), codex: path("codex"),
                    model: manifest.entrypoints["model"].map { root.appendingPathComponent($0).path },
                    device: tree.device, inode: tree.inode, signedBundle: nil)
    }

    /// Used immediately before a child launch and before login registration.
    public func revalidate() throws {
        if let signedBundle { try Self.validateSignature(signedBundle) }
        var current = try Self.resolve(root: root, manifestSHA256: manifestSHA256)
        current.signedBundle = signedBundle
        guard current == self else { throw BundledRuntimeFailure.changed }
    }

    public static func fromSignedBundle(_ bundle: Bundle) throws -> Self {
        try fromSignedBundle(url: bundle.bundleURL, resource: bundle.resourceURL,
            executable: bundle.object(forInfoDictionaryKey: "CFBundleExecutable") as? String,
            pin: bundle.object(forInfoDictionaryKey: "JarvisRuntimeManifestSHA256") as? String)
    }

    static func fromSignedBundle(url: URL, resource: URL?, executable: String?, pin: String?) throws -> Self {
        guard url.pathExtension == "app", executable == "Jarvis", let resource, let pin else {
            throw BundledRuntimeFailure.missing
        }
        try validateSignature(url)
        #if !arch(arm64)
        throw BundledRuntimeFailure.unsupported
        #else
        guard ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 14 else { throw BundledRuntimeFailure.unsupported }
        var result = try resolve(root: resource.appendingPathComponent("JarvisRuntime", isDirectory: true), manifestSHA256: pin)
        result.signedBundle = url
        return result
        #endif
    }

    private static func validateSignature(_ url: URL) throws {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, SecCSFlags(rawValue: 0), &code) == errSecSuccess,
              let code, SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSStrictValidate | kSecCSCheckAllArchitectures), nil) == errSecSuccess else {
            throw BundledRuntimeFailure.signature
        }
    }

    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

/// Descriptor-relative reads reject links at every component. Tree inventories
/// are closed: inner code signatures are files like any other, never exclusions.
private final class RuntimeFileTree {
    let root: URL
    let descriptor: Int32
    let device: UInt64
    let inode: UInt64
    private let deadline = ProcessInfo.processInfo.systemUptime + 60

    init(_ root: URL) throws {
        try Task.checkCancellation()
        let path = root.path
        let fd = try BundledRuntimePath.openDirectory(path)
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o022 == 0 else {
            Darwin.close(fd); throw BundledRuntimeFailure.insecureTree
        }
        self.root = root; descriptor = fd; device = UInt64(info.st_dev); inode = UInt64(info.st_ino)
    }
    deinit { Darwin.close(descriptor) }

    private func openFile(_ relative: String) throws -> Int32 {
        guard BundledRuntimeManifest.relativePath(relative) else { throw BundledRuntimeFailure.invalidManifest }
        let parts = relative.split(separator: "/").map(String.init)
        var current = fcntl(descriptor, F_DUPFD_CLOEXEC, 0)
        guard current >= 0 else { throw BundledRuntimeFailure.insecureTree }
        defer { Darwin.close(current) }
        for part in parts.dropLast() {
            let next = openat(current, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else { throw BundledRuntimeFailure.insecureTree }
            var info = stat()
            guard fstat(next, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
                  info.st_mode & 0o022 == 0 else { Darwin.close(next); throw BundledRuntimeFailure.insecureTree }
            Darwin.close(current); current = next
        }
        let fd = openat(current, parts.last!, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { throw BundledRuntimeFailure.missing }
        return fd
    }

    func read(_ path: String, limit: Int) throws -> Data {
        try checkDeadline()
        let fd = try openFile(path); defer { Darwin.close(fd) }
        let before = try fileInfo(fd)
        guard before.st_size >= 0, before.st_size <= limit else { throw BundledRuntimeFailure.invalidManifest }
        var data = Data()
        while true {
            try checkDeadline()
            var chunk = [UInt8](repeating: 0, count: 65_536)
            let count = Darwin.read(fd, &chunk, chunk.count)
            if count < 0 { if errno == EINTR { continue }; throw BundledRuntimeFailure.changed }
            if count == 0 { break }
            guard data.count <= limit - count else { throw BundledRuntimeFailure.invalidManifest }
            data.append(contentsOf: chunk.prefix(count))
        }
        guard unchanged(before, try fileInfo(fd)), data.count == Int(before.st_size) else { throw BundledRuntimeFailure.changed }
        return data
    }

    func validate(_ manifest: BundledRuntimeManifest) throws {
        guard let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil, options: [], errorHandler: { _, _ in false }) else {
            throw BundledRuntimeFailure.insecureTree
        }
        var actual: Set<String> = []
        var directories: Set<String> = []
        var count = 0
        for case let url as URL in enumerator {
            try checkDeadline()
            count += 1
            guard count <= BundledRuntimeManifest.maximumFiles * 4,
                  url.path.hasPrefix(root.path + "/") else { throw BundledRuntimeFailure.insecureTree }
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard BundledRuntimeManifest.relativePath(relative) else { throw BundledRuntimeFailure.insecureTree }
            var info = stat()
            guard lstat(url.path, &info) == 0, info.st_mode & 0o022 == 0 else { throw BundledRuntimeFailure.insecureTree }
            if info.st_mode & S_IFMT == S_IFDIR { directories.insert(relative) }
            else if info.st_mode & S_IFMT == S_IFREG { actual.insert(relative) }
            else { throw BundledRuntimeFailure.insecureTree }
        }
        guard actual == Set(manifest.files.keys).union(["runtime-manifest.json"]) else { throw BundledRuntimeFailure.changed }
        var expectedDirectories: Set<String> = []
        for path in actual {
            let parts = path.split(separator: "/")
            for length in 1..<parts.count { expectedDirectories.insert(parts.prefix(length).joined(separator: "/")) }
        }
        guard directories == expectedDirectories else { throw BundledRuntimeFailure.changed }
        for row in manifest.files.values {
            try checkDeadline()
            let fd = try openFile(row.path); defer { Darwin.close(fd) }
            let before = try fileInfo(fd)
            guard before.st_size == row.bytes, (before.st_mode & 0o111 != 0) == row.executable else { throw BundledRuntimeFailure.changed }
            var hash = SHA256()
            var bytes = 0
            var prefix = Data()
            while true {
                try checkDeadline()
                var chunk = [UInt8](repeating: 0, count: 65_536)
                let count = Darwin.read(fd, &chunk, chunk.count)
                if count < 0 { if errno == EINTR { continue }; throw BundledRuntimeFailure.changed }
                if count == 0 { break }
                bytes += count
                guard bytes <= row.bytes else { throw BundledRuntimeFailure.changed }
                hash.update(data: Data(chunk.prefix(count)))
                if prefix.count < 16 { prefix.append(contentsOf: chunk.prefix(min(count, 16 - prefix.count))) }
            }
            let digest = hash.finalize().map { String(format: "%02x", $0) }.joined()
            guard bytes == row.bytes, digest == row.sha256,
                  unchanged(before, try fileInfo(fd)), unchanged(before, try namedFileInfo(row.path)) else { throw BundledRuntimeFailure.changed }
            if row.path == manifest.entrypoints["python"] || row.path == manifest.entrypoints["codex"] {
                guard Array(prefix) == [0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0, 0, 1, 0, 0, 0, 0, 2, 0, 0, 0] else {
                    throw BundledRuntimeFailure.unsupported
                }
            }
        }
        var named = stat()
        guard lstat(root.path, &named) == 0, UInt64(named.st_dev) == device, UInt64(named.st_ino) == inode else {
            throw BundledRuntimeFailure.changed
        }
    }

    private func fileInfo(_ fd: Int32) throws -> stat {
        var value = stat()
        guard fstat(fd, &value) == 0, value.st_mode & S_IFMT == S_IFREG,
              value.st_nlink == 1, value.st_mode & 0o022 == 0 else { throw BundledRuntimeFailure.insecureTree }
        return value
    }
    private func namedFileInfo(_ path: String) throws -> stat {
        let fd = try openFile(path); defer { Darwin.close(fd) }
        return try fileInfo(fd)
    }
    private func checkDeadline() throws {
        try Task.checkCancellation()
        guard ProcessInfo.processInfo.systemUptime < deadline else { throw BundledRuntimeFailure.validationTimeout }
    }
    private func unchanged(_ lhs: stat, _ rhs: stat) -> Bool {
        lhs.st_dev == rhs.st_dev && lhs.st_ino == rhs.st_ino && lhs.st_size == rhs.st_size
            && lhs.st_mode == rhs.st_mode && lhs.st_mtimespec.tv_sec == rhs.st_mtimespec.tv_sec
            && lhs.st_mtimespec.tv_nsec == rhs.st_mtimespec.tv_nsec && lhs.st_ctimespec.tv_sec == rhs.st_ctimespec.tv_sec
            && lhs.st_ctimespec.tv_nsec == rhs.st_ctimespec.tv_nsec
    }
}
