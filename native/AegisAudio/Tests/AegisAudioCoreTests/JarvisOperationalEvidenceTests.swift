import Foundation
import Testing
@testable import AegisAudioCore

@Test func operationalEvidencePersistsOnlyBoundedMetrics() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-evidence-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-evidence.json")
    defer { try? FileManager.default.removeItem(at: directory) }
    let recorder = JarvisOperationalEvidenceRecorder(destination: destination)
    let observedAt = Date(timeIntervalSince1970: 1_750_000_000)

    try await recorder.recordVoiceTurn(
        ownerVerified: true,
        firstPartialMilliseconds: 120,
        totalMilliseconds: 480,
        at: observedAt
    )
    try await recorder.recordScreenCapture(
        milliseconds: 90,
        payloadBytes: 24_000,
        at: observedAt
    )
    try await recorder.recordComputerAction(
        verified: true,
        milliseconds: 75,
        at: observedAt
    )

    let snapshot = try await recorder.current()
    let stored = String(decoding: try Data(contentsOf: destination), as: UTF8.self)
    let attributes = try FileManager.default.attributesOfItem(atPath: destination.path)
    #expect(snapshot.voiceTurns == 1)
    #expect(snapshot.buildRevision == JarvisBuildIdentity.development)
    #expect(snapshot.ownerVerifiedVoiceTurns == 1)
    #expect(snapshot.screenCaptures == 1)
    #expect(snapshot.computerActions == 1)
    #expect(snapshot.verifiedComputerActions == 1)
    #expect(snapshot.isValid)
    #expect(attributes[.posixPermissions] as? Int == 0o600)
    #expect(!stored.contains("transcript"))
    #expect(!stored.contains("prompt"))
    #expect(!stored.contains("url"))
    #expect(stored.contains("\"build_revision\":\"development\""))
}

@Test func operationalEvidenceResetsWhenTheNativeBuildChanges() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-evidence-build-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-evidence.json")
    defer { try? FileManager.default.removeItem(at: directory) }
    let firstRevision = String(repeating: "a", count: 40)
    let secondRevision = String(repeating: "b", count: 40)

    let firstRecorder = JarvisOperationalEvidenceRecorder(
        destination: destination,
        buildRevision: firstRevision
    )
    try await firstRecorder.recordVoiceTurn(
        ownerVerified: true,
        firstPartialMilliseconds: 80,
        totalMilliseconds: 200
    )

    let secondRecorder = JarvisOperationalEvidenceRecorder(
        destination: destination,
        buildRevision: secondRevision
    )
    let reset = try await secondRecorder.current()
    #expect(reset.buildRevision == secondRevision)
    #expect(reset.voiceTurns == 0)
    #expect(reset.ownerVerifiedVoiceTurns == 0)

    try await secondRecorder.recordComputerAction(verified: true, milliseconds: 60)
    let reloaded = try await JarvisOperationalEvidenceRecorder(
        destination: destination,
        buildRevision: secondRevision
    ).current()
    #expect(reloaded.buildRevision == secondRevision)
    #expect(reloaded.computerActions == 1)
    #expect(reloaded.verifiedComputerActions == 1)
}

@Test func operationalEvidenceRejectsInvalidMetricsAndCorruptState() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-evidence-invalid-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-evidence.json")
    defer { try? FileManager.default.removeItem(at: directory) }
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    try Data("{}".utf8).write(to: destination)
    let corruptRecorder = JarvisOperationalEvidenceRecorder(destination: destination)

    await #expect(throws: JarvisOperationalEvidenceError.self) {
        _ = try await corruptRecorder.current()
    }

    try FileManager.default.removeItem(at: destination)
    let recorder = JarvisOperationalEvidenceRecorder(destination: destination)
    await #expect(throws: JarvisOperationalEvidenceError.invalidMetric) {
        try await recorder.recordScreenCapture(milliseconds: 1, payloadBytes: 32_769)
    }
    await #expect(throws: JarvisOperationalEvidenceError.invalidMetric) {
        try await recorder.recordVoiceTurn(
            ownerVerified: true,
            firstPartialMilliseconds: 501,
            totalMilliseconds: 500
        )
    }
}

@Test func operationalEvidenceRejectsSymbolicLinkDestination() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("jarvis-evidence-link-\(UUID().uuidString)", isDirectory: true)
    let destination = directory.appendingPathComponent("runtime-evidence.json")
    let target = directory.appendingPathComponent("target.json")
    defer { try? FileManager.default.removeItem(at: directory) }
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    try Data("{}".utf8).write(to: target)
    try FileManager.default.createSymbolicLink(at: destination, withDestinationURL: target)
    let recorder = JarvisOperationalEvidenceRecorder(destination: destination)

    await #expect(throws: JarvisOperationalEvidenceError.unsafeDestination) {
        _ = try await recorder.current()
    }
}
