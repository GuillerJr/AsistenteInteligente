import CryptoKit
import Darwin
import Foundation

/// The daemon resolves an opaque token; model text can never choose a local path.
public struct GeneratedMediaArtifact: Sendable, Equatable {
    public let token: String
    public let fileName: String
    public let mediaType: String
    public let byteCount: Int
    public let sha256: String
    public let url: URL
    public static let defaultDirectory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Aegis/generated-media", isDirectory: true)

    public init(_ data: Data, token expectedToken: String,
                directory: URL = Self.defaultDirectory) throws {
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = value["token"] as? String, token == expectedToken,
              token.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              let mediaType = value["media_type"] as? String,
              ["image/jpeg", "video/mp4"].contains(mediaType),
              let fileName = value["file_name"] as? String,
              fileName == "jarvis-cosmos-\(token).\(mediaType == "image/jpeg" ? "jpg" : "mp4")",
              let byteCount = value["byte_count"] as? Int, (12...48_000_000).contains(byteCount),
              let sha256 = value["sha256"] as? String,
              sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
              value["model_id"] as? String == "nvidia/cosmos3-nano",
              let path = value["path"] as? String,
              path == directory.appendingPathComponent(fileName).path
        else { throw LocalIPCError.malformedResponse }
        self.token = token; self.fileName = fileName; self.mediaType = mediaType
        self.byteCount = byteCount; self.sha256 = sha256; self.url = URL(fileURLWithPath: path)
    }

    public func read() throws -> Data {
        let directory = url.deletingLastPathComponent()
        var directoryInfo = stat()
        guard lstat(directory.path, &directoryInfo) == 0,
              directoryInfo.st_uid == geteuid(), (directoryInfo.st_mode & S_IFMT) == S_IFDIR,
              (directoryInfo.st_mode & 0o077) == 0 else { throw LocalIPCError.malformedResponse }
        let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        guard fd >= 0 else { throw LocalIPCError.malformedResponse }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == geteuid(),
              (info.st_mode & S_IFMT) == S_IFREG, (info.st_mode & 0o077) == 0,
              info.st_nlink == 1, info.st_size == byteCount else { throw LocalIPCError.malformedResponse }
        var data = Data(count: byteCount)
        try data.withUnsafeMutableBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let count = Darwin.read(fd, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                if count > 0 { offset += count }
                else if count < 0 && errno == EINTR { continue }
                else { throw LocalIPCError.malformedResponse }
            }
        }
        guard SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == sha256
        else { throw LocalIPCError.malformedResponse }
        return data
    }

    public static func tokens(in text: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: #"\[[^\]\n]{1,120}\]\(jarvis-artifact://([0-9a-f]{32})\)"#)
        else { return [] }
        var tokens: [String] = []
        for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(match.range(at: 1), in: text) else { continue }
            let token = String(text[range])
            if !tokens.contains(token) { tokens.append(token) }
            if tokens.count == 4 { break }
        }
        return tokens
    }
}
