import Foundation
import Testing
@testable import AegisAudioCore

@Test func enrollmentStoreCreatesPrivateBoundedLabelDirectories() throws {
    let root = FileManager.default.temporaryDirectory
        .appending(path: "jarvis-enrollment-\(UUID().uuidString)", directoryHint: .isDirectory)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = WakeWordEnrollmentStore(rootURL: root)

    let progress = try store.progress()

    #expect(progress == WakeWordEnrollmentProgress(jarvisCount: 0, backgroundCount: 0))
    for directory in [root, root.appending(path: "jarvis"), root.appending(path: "background")] {
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.path)
        #expect(attributes[.posixPermissions] as? Int == 0o700)
    }
}

@Test func enrollmentStoreCountsOnlyCommittedPrivateSamples() throws {
    let root = FileManager.default.temporaryDirectory
        .appending(path: "jarvis-enrollment-\(UUID().uuidString)", directoryHint: .isDirectory)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = WakeWordEnrollmentStore(rootURL: root)
    try store.prepare()
    let temporary = store.makeTemporaryURL()
    try Data([0x63, 0x61, 0x66, 0x66]).write(to: temporary)

    try store.commit(temporary, label: .jarvis)

    #expect(try store.progress().jarvisCount == 1)
    #expect(try store.progress().backgroundCount == 0)
    let sample = try #require(
        FileManager.default.contentsOfDirectory(
            at: root.appending(path: "jarvis"),
            includingPropertiesForKeys: nil
        ).first
    )
    let attributes = try FileManager.default.attributesOfItem(atPath: sample.path)
    #expect(attributes[.posixPermissions] as? Int == 0o600)
}

@Test func enrollmentStoreRejectsSymlinkedRoot() throws {
    let container = FileManager.default.temporaryDirectory
        .appending(path: "jarvis-enrollment-\(UUID().uuidString)", directoryHint: .isDirectory)
    let actual = container.appending(path: "actual", directoryHint: .isDirectory)
    let link = container.appending(path: "link", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: actual, withIntermediateDirectories: true)
    try FileManager.default.createSymbolicLink(at: link, withDestinationURL: actual)
    defer { try? FileManager.default.removeItem(at: container) }

    #expect(throws: WakeWordEnrollmentError.unsafeStorage) {
        try WakeWordEnrollmentStore(rootURL: link).prepare()
    }
}

@Test func enrollmentQualityRejectsQuietPositiveSample() {
    var quality = WakeWordSampleQualityAccumulator()
    quality.consume(rms: 0.001, peak: 0.01, frameCount: 48_000)

    #expect(throws: WakeWordEnrollmentError.sampleTooQuiet) {
        try quality.validate(
            label: .jarvis,
            minimumAnalyzedFrames: 19_200,
            minimumAudibleFrames: 5_760
        )
    }
}

@Test func enrollmentQualityAcceptsSilentBackground() throws {
    var quality = WakeWordSampleQualityAccumulator()
    quality.consume(rms: 0, peak: 0, frameCount: 48_000)

    try quality.validate(
        label: .background,
        minimumAnalyzedFrames: 19_200,
        minimumAudibleFrames: 5_760
    )
}

@Test func enrollmentQualityRejectsClippingAndAcceptsAudibleKeyword() throws {
    var clipped = WakeWordSampleQualityAccumulator()
    clipped.consume(rms: 0.2, peak: 1, frameCount: 48_000)
    #expect(throws: WakeWordEnrollmentError.sampleClipped) {
        try clipped.validate(
            label: .jarvis,
            minimumAnalyzedFrames: 19_200,
            minimumAudibleFrames: 5_760
        )
    }

    var audible = WakeWordSampleQualityAccumulator()
    audible.consume(rms: 0.02, peak: 0.2, frameCount: 6_000)
    audible.consume(rms: 0.001, peak: 0.01, frameCount: 42_000)
    try audible.validate(
        label: .jarvis,
        minimumAnalyzedFrames: 19_200,
        minimumAudibleFrames: 5_760
    )
}
