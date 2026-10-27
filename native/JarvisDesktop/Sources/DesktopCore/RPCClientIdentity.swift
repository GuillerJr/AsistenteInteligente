import CryptoKit
import Darwin
import Foundation

/// A profile-selected key authenticates client possession. It is not a macOS
/// application code signature or protection against a compromised login user.
public struct RPCClientIdentity: Sendable {
    private let key: Curve25519.Signing.PrivateKey
    public var publicKey: String { key.publicKey.rawRepresentation.map { String(format: "%02x", $0) }.joined() }
    public var clientID: String { SHA256.hash(data: key.publicKey.rawRepresentation).map { String(format: "%02x", $0) }.joined() }

    public init(raw: Data) throws {
        guard raw.count == 32 else { throw RPCFailure.insecurePath }
        key = try Curve25519.Signing.PrivateKey(rawRepresentation: raw)
    }

    public static func forProfile(_ directory: String, create: Bool = false) throws -> Self {
        if let explicit = ProcessInfo.processInfo.environment["JARVIS_RPC_CLIENT_KEY_FILE"] {
            return try load(explicit)
        }
        let parent = directory + ".clients"
        if create {
            if mkdir(parent, 0o700) != 0, errno != EEXIST { throw RPCFailure.insecurePath }
            try checkDirectory(parent)
            let descriptor = Darwin.open(parent + "/desktop.key", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
            if descriptor >= 0 {
                defer { Darwin.close(descriptor) }
                let generated = Curve25519.Signing.PrivateKey().rawRepresentation
                let written = generated.withUnsafeBytes { Darwin.write(descriptor, $0.baseAddress, $0.count) }
                guard written == 32, fsync(descriptor) == 0 else { throw RPCFailure.insecurePath }
                let parentFD = Darwin.open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
                guard parentFD >= 0 else { throw RPCFailure.insecurePath }
                defer { Darwin.close(parentFD) }
                guard fsync(parentFD) == 0 else { throw RPCFailure.insecurePath }
            } else if errno != EEXIST { throw RPCFailure.insecurePath }
        }
        return try load(parent + "/desktop.key")
    }

    private static func checkDirectory(_ directory: String) throws {
        guard directory.hasPrefix("/"), !directory.contains("//"), !directory.hasSuffix("/"),
              !directory.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }) else { throw RPCFailure.configuration }
        var path = directory
        while path != "/" {
            var info = stat()
            guard lstat(path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR else { throw RPCFailure.insecurePath }
            if path == directory {
                guard info.st_uid == getuid(), info.st_mode & 0o777 == 0o700 else { throw RPCFailure.insecurePath }
            }
            guard let index = path.lastIndex(of: "/") else { throw RPCFailure.insecurePath }
            path = index == path.startIndex ? "/" : String(path[..<index])
        }
    }

    public static func load(_ path: String) throws -> Self {
        let url = URL(fileURLWithPath: path)
        try checkDirectory(url.deletingLastPathComponent().path)
        let descriptor = Darwin.open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw RPCFailure.unavailable }
        defer { Darwin.close(descriptor) }
        var before = stat(), after = stat()
        guard fstat(descriptor, &before) == 0, before.st_mode & S_IFMT == S_IFREG,
              before.st_uid == getuid(), before.st_mode & 0o777 == 0o600,
              before.st_nlink == 1, before.st_size == 32 else { throw RPCFailure.insecurePath }
        var bytes = [UInt8](repeating: 0, count: 33)
        guard Darwin.read(descriptor, &bytes, bytes.count) == 32,
              lstat(path, &after) == 0, after.st_dev == before.st_dev, after.st_ino == before.st_ino,
              after.st_mtimespec.tv_sec == before.st_mtimespec.tv_sec,
              after.st_mtimespec.tv_nsec == before.st_mtimespec.tv_nsec,
              after.st_ctimespec.tv_sec == before.st_ctimespec.tv_sec,
              after.st_ctimespec.tv_nsec == before.st_ctimespec.tv_nsec else { throw RPCFailure.insecurePath }
        return try Self(raw: Data(bytes.prefix(32)))
    }

    public static func profileBinding(_ directory: String) throws -> String {
        try checkDirectory(directory)
        var info = stat()
        guard lstat(directory, &info) == 0 else { throw RPCFailure.insecurePath }
        let bytes = Data("jarvis-client-profile/1:\(info.st_dev):\(info.st_ino)".utf8)
        return SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    public func validateChallenge(_ value: RPCValue, directory: String) throws {
        guard let object = value.object,
              Set(object.keys) == ["client_id", "profile_binding", "server_id", "nonce"],
              value["client_id"].string == clientID,
              value["profile_binding"].string == (try Self.profileBinding(directory)),
              object.values.allSatisfy({ $0.string?.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil }) else {
            throw RPCFailure.invalidResponse
        }
    }

    public func signedFrame(id: Int, method: String, params: [String: RPCValue], token: String,
                            challenge: RPCValue, sequence: Int) throws -> Data {
        guard sequence > 0 else { throw RPCFailure.configuration }
        let inner = RPCValue.object(["jsonrpc": .string("2.0"), "id": .number(Double(id)),
                                     "method": .string(method), "params": .object(params)])
        let body = try JSONEncoder().encode(inner)
        let digest = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        let payload = ["jarvis-client-signature/1", challenge["profile_binding"].string ?? "",
                       challenge["server_id"].string ?? "", challenge["nonce"].string ?? "",
                       clientID, String(sequence), digest].joined(separator: "\n")
        let signature = try key.signature(for: Data(payload.utf8)).map { String(format: "%02x", $0) }.joined()
        return try RPCFrame.request(id: id, method: "client.request", params: [
            "body": .string(body.base64EncodedString()), "sequence": .number(Double(sequence)),
            "signature": .string(signature)], token: token)
    }
}
