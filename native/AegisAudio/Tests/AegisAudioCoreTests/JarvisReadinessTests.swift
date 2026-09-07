import Foundation
import Testing
@testable import AegisAudioCore

@Test func readinessSnapshotPersistsPrivatelyWithoutUserContent() throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-readiness-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-readiness.json")
    defer { try? FileManager.default.removeItem(at: directory) }
    let snapshot = JarvisReadinessSnapshot(
        daemon: "online",
        security: "intact",
        provider: "configured",
        localBrainAvailable: true,
        microphone: "authorized",
        speechRecognition: "authorized",
        screenCaptureAuthorized: true,
        computerControl: "ready",
        wakeWord: "ready",
        wakeWordEnabled: true,
        speakerIdentity: "ready"
    )

    try JarvisReadinessStore.persist(snapshot, to: destination)

    let stored = try JSONDecoder().decode(
        JarvisReadinessSnapshot.self,
        from: Data(contentsOf: destination)
    )
    let attributes = try FileManager.default.attributesOfItem(atPath: destination.path)
    #expect(stored == snapshot)
    #expect(stored.buildRevision == JarvisBuildIdentity.development)
    #expect(attributes[.posixPermissions] as? Int == 0o600)
    #expect(!String(decoding: try Data(contentsOf: destination), as: UTF8.self)
        .contains("transcript"))
}

@Test func readinessStoreRejectsInvalidBuildRevision() throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-readiness-build-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-readiness.json")
    defer { try? FileManager.default.removeItem(at: directory) }

    #expect(throws: JarvisReadinessStoreError.invalidBuildRevision) {
        try JarvisReadinessStore.persist(
            JarvisReadinessSnapshot(
                daemon: "online",
                security: "intact",
                provider: "configured",
                localBrainAvailable: true,
                microphone: "authorized",
                speechRecognition: "authorized",
                screenCaptureAuthorized: true,
                computerControl: "ready",
                wakeWord: "ready",
                wakeWordEnabled: true,
                speakerIdentity: "ready",
                buildRevision: "not-a-revision"
            ),
            to: destination
        )
    }
}

@Test func readinessStoreRejectsSymbolicLinkDestination() throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-readiness-link-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-readiness.json")
    let target = directory.appendingPathComponent("target.json")
    defer { try? FileManager.default.removeItem(at: directory) }
    try FileManager.default.createDirectory(
        at: directory,
        withIntermediateDirectories: true
    )
    try Data("{}".utf8).write(to: target)
    try FileManager.default.createSymbolicLink(at: destination, withDestinationURL: target)

    #expect(throws: JarvisReadinessStoreError.unsafeDestination) {
        try JarvisReadinessStore.persist(
            JarvisReadinessSnapshot(
                daemon: "offline",
                security: "unknown",
                provider: "unknown",
                localBrainAvailable: false,
                microphone: "denied",
                speechRecognition: "denied",
                screenCaptureAuthorized: false,
                computerControl: "permissions_missing",
                wakeWord: "missing",
                wakeWordEnabled: false,
                speakerIdentity: "missing"
            ),
            to: destination
        )
    }
}
