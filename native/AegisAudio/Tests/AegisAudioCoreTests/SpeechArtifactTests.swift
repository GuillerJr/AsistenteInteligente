import CryptoKit
import Darwin
import Foundation
import Testing
@testable import AegisAudioCore

private func speechTestDirectory() throws -> URL {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-speech-tests-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
    guard chmod(directory.path, 0o700) == 0 else {
        throw SpeechArtifactReaderError.unsafeDirectory
    }
    return directory
}

private func minimalWaveData() -> Data {
    var data = Data(repeating: 0, count: 44)
    data.replaceSubrange(0 ..< 4, with: Data("RIFF".utf8))
    data.replaceSubrange(8 ..< 12, with: Data("WAVE".utf8))
    return data
}

private func speechArtifactEvent(
    token: String,
    data: Data,
    digest: String? = nil
) throws -> IPCSpeechArtifactEvent {
    let response = LocalIPCResponse(
        requestID: UUID(),
        ok: true,
        payload: [
            "token": token,
            "file_name": "jarvis-tts-\(token).wav",
            "sha256": digest ?? SHA256.hash(data: data).map {
                String(format: "%02x", $0)
            }.joined(),
            "byte_count": data.count,
        ],
        errorCode: nil
    )
    return try #require(IPCSpeechArtifactEvent(response: response))
}

@Test func speechArtifactReaderLoadsValidatedPrivateWave() throws {
    let directory = try speechTestDirectory()
    defer { try? FileManager.default.removeItem(at: directory) }
    let token = String(repeating: "a", count: 32)
    let data = minimalWaveData()
    let url = directory.appendingPathComponent("jarvis-tts-\(token).wav")
    try data.write(to: url, options: .withoutOverwriting)
    #expect(chmod(url.path, 0o600) == 0)

    let reader = try SpeechArtifactReader(directory: directory)
    let artifact = try speechArtifactEvent(token: token, data: data)

    #expect(try reader.read(artifact) == data)
}

@Test func speechArtifactReaderRejectsDigestMismatchAndUnsafeMode() throws {
    let directory = try speechTestDirectory()
    defer { try? FileManager.default.removeItem(at: directory) }
    let token = String(repeating: "b", count: 32)
    let data = minimalWaveData()
    let url = directory.appendingPathComponent("jarvis-tts-\(token).wav")
    try data.write(to: url, options: .withoutOverwriting)
    #expect(chmod(url.path, 0o600) == 0)
    let reader = try SpeechArtifactReader(directory: directory)
    let badDigest = try speechArtifactEvent(
        token: token,
        data: data,
        digest: String(repeating: "c", count: 64)
    )

    #expect(throws: SpeechArtifactReaderError.digestMismatch) {
        try reader.read(badDigest)
    }
    #expect(chmod(url.path, 0o644) == 0)
    let artifact = try speechArtifactEvent(token: token, data: data)
    #expect(throws: SpeechArtifactReaderError.unsafeArtifact) {
        try reader.read(artifact)
    }
}

@Test func speechArtifactReaderRejectsSymlinkArtifact() throws {
    let directory = try speechTestDirectory()
    defer { try? FileManager.default.removeItem(at: directory) }
    let token = String(repeating: "d", count: 32)
    let data = minimalWaveData()
    let target = directory.appendingPathComponent("target.wav")
    try data.write(to: target, options: .withoutOverwriting)
    #expect(chmod(target.path, 0o600) == 0)
    let linked = directory.appendingPathComponent("jarvis-tts-\(token).wav")
    try FileManager.default.createSymbolicLink(at: linked, withDestinationURL: target)

    let reader = try SpeechArtifactReader(directory: directory)
    let artifact = try speechArtifactEvent(token: token, data: data)
    #expect(throws: SpeechArtifactReaderError.unsafeArtifact) {
        try reader.read(artifact)
    }
}
