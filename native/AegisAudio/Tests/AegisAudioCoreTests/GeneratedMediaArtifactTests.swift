import CryptoKit
import Darwin
import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Generated media originals")
struct GeneratedMediaArtifactTests {
    private let token = String(repeating: "a", count: 32)
    private let bytes = Data("synthetic original media bytes".utf8)

    private func fixture() throws -> (URL, [String: Any]) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        let file = root.appendingPathComponent("jarvis-cosmos-\(token).jpg")
        try bytes.write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        return (root, ["token": token, "file_name": file.lastPathComponent,
                       "media_type": "image/jpeg", "byte_count": bytes.count,
                       "sha256": SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
                       "model_id": "nvidia/cosmos3-nano", "path": file.path])
    }

    private func decode(_ value: [String: Any], root: URL) throws -> GeneratedMediaArtifact {
        try GeneratedMediaArtifact(JSONSerialization.data(withJSONObject: value), token: token,
                                   directory: root)
    }

    @Test func originalIsReadWithoutModification() throws {
        let (root, value) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(try decode(value, root: root).read() == bytes)
    }

    @Test(arguments: ["path", "token", "file_name", "sha256", "model_id", "byte_count"])
    func forgedMetadataIsRejected(field: String) throws {
        let (root, original) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        var value = original
        value[field] = field == "byte_count" ? 48_000_001 : "forged"
        #expect(throws: LocalIPCError.self) { try decode(value, root: root) }
    }

    @Test(arguments: ["tamper", "symlink", "permissions", "hardlink", "directory"])
    func unsafeOriginalIsRejected(change: String) throws {
        let (root, value) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let artifact = try decode(value, root: root)
        switch change {
        case "tamper":
            try Data(repeating: 0, count: bytes.count).write(to: artifact.url)
        case "symlink":
            let other = root.appendingPathComponent("other.jpg")
            try FileManager.default.moveItem(at: artifact.url, to: other)
            try FileManager.default.createSymbolicLink(at: artifact.url, withDestinationURL: other)
        case "permissions":
            try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: artifact.url.path)
        case "hardlink":
            #expect(link(artifact.url.path, root.appendingPathComponent("duplicate.jpg").path) == 0)
        default:
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root.path)
        }
        #expect(throws: LocalIPCError.self) { try artifact.read() }
    }

    @Test func onlyCanonicalLinksProduceUniqueBoundedTokens() {
        let valid = "[Imagen](jarvis-artifact://\(token))"
        #expect(GeneratedMediaArtifact.tokens(in: "\(valid) \(valid)") == [token])
        #expect(GeneratedMediaArtifact.tokens(in: "jarvis-artifact://\(token) [bad](jarvis-artifact://../../file)").isEmpty)
        let many = (1...8).map { "[Video](jarvis-artifact://\(String(format: "%032x", $0)))" }.joined()
        #expect(GeneratedMediaArtifact.tokens(in: many).count == 4)
    }
}
