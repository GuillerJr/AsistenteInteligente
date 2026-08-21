import Foundation
import Testing
@testable import AegisAudioCore

@Test func missingWakeWordModelFailsClosed() {
    #expect(WakeWordCapability.inspect(modelURL: nil) == .missing)
}

@Test func nonCompiledWakeWordAssetIsInvalid() throws {
    let root = FileManager.default.temporaryDirectory
        .appending(path: "jarvis-wake-word-\(UUID().uuidString)", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: root) }

    #expect(WakeWordCapability.inspect(modelURL: root) == .invalid)
}

@Test func emptyCompiledWakeWordDirectoryIsInvalid() throws {
    let modelURL = FileManager.default.temporaryDirectory
        .appending(path: "JarvisWakeWord-\(UUID().uuidString).mlmodelc", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: modelURL, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: modelURL) }

    #expect(WakeWordCapability.inspect(modelURL: modelURL) == .invalid)
}
