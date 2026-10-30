import Darwin
import Foundation

/// Validate the spelling and walk physical directories without Foundation's
/// private-prefix normalization. `/private/tmp` is an admitted physical path;
/// `/tmp` is a symlink and is not silently turned into another spelling.
enum BundledRuntimePath {
    static func validateSpelling(_ path: String) throws {
        guard path.hasPrefix("/"), path.utf8.count <= 4096,
              !path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              path == "/" || path.split(separator: "/", omittingEmptySubsequences: false).dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
            throw BundledRuntimeFailure.insecureTree
        }
    }

    static func openDirectory(_ path: String) throws -> Int32 {
        try validateSpelling(path)
        var current = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard current >= 0 else { throw BundledRuntimeFailure.insecureTree }
        do {
            for component in path.split(separator: "/") {
                let next = openat(current, String(component), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
                guard next >= 0 else { throw BundledRuntimeFailure.insecureTree }
                Darwin.close(current); current = next
            }
            return current
        } catch {
            Darwin.close(current)
            throw error
        }
    }

    static func validateDirectory(_ path: String) throws {
        let fd = try openDirectory(path)
        Darwin.close(fd)
    }

    static func validateFile(_ path: String) throws {
        try validateSpelling(path)
        guard let slash = path.lastIndex(of: "/"), slash != path.index(before: path.endIndex) else {
            throw BundledRuntimeFailure.insecureTree
        }
        let parentPath = String(path[..<slash])
        let parent = try openDirectory(parentPath.isEmpty ? "/" : parentPath)
        defer { Darwin.close(parent) }
        let name = String(path[path.index(after: slash)...])
        let fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { throw BundledRuntimeFailure.insecureTree }
        defer { Darwin.close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG else { throw BundledRuntimeFailure.insecureTree }
    }
}
