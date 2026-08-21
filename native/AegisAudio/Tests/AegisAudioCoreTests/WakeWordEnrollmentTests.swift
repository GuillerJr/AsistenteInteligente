import Foundation
import Testing
@testable import AegisAudioCore

@Test func enrollmentGuidanceRotatesPositiveConditions() {
    #expect(WakeWordEnrollmentGuidance.instruction(label: .jarvis, acceptedCount: -1) == "Voz normal, a tu distancia habitual")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .jarvis, acceptedCount: 5) == "Voz ligeramente más baja")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .jarvis, acceptedCount: 10) == "Cambia la distancia o gira un poco la cabeza")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .jarvis, acceptedCount: 15) == "Voz normal con el ruido cotidiano presente")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .jarvis, acceptedCount: 20) == "Añade otra variación natural")
}

@Test func enrollmentGuidanceAddsHardNegativeConditions() {
    #expect(WakeWordEnrollmentGuidance.instruction(label: .background, acceptedCount: 0) == "Captura silencio real de la habitación")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .background, acceptedCount: 5) == "Captura ruido cotidiano sin hablar")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .background, acceptedCount: 10) == "Habla con normalidad sin usar la palabra de activación")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .background, acceptedCount: 15) == "Di palabras parecidas: “Javier”, “viernes” o “jardín”")
    #expect(WakeWordEnrollmentGuidance.instruction(label: .background, acceptedCount: 20) == "Añade otro sonido habitual distinto")
}

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

@Test func enrollmentStoreClearsOnlyValidatedSamplesAndKeepsPrivateDirectories() throws {
    let root = FileManager.default.temporaryDirectory
        .appending(path: "jarvis-enrollment-\(UUID().uuidString)", directoryHint: .isDirectory)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = WakeWordEnrollmentStore(rootURL: root)
    try store.prepare()
    for label in WakeWordEnrollmentLabel.allCases {
        let temporary = store.makeTemporaryURL()
        try Data([0x63, 0x61, 0x66, 0x66]).write(to: temporary)
        try store.commit(temporary, label: label)
    }

    let progress = try store.clearSamples()
    #expect(progress == WakeWordEnrollmentProgress(
        jarvisCount: 0,
        backgroundCount: 0
    ))
    for directory in [root, root.appending(path: "jarvis"), root.appending(path: "background")] {
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.path)
        #expect(attributes[.posixPermissions] as? Int == 0o700)
    }
}

@Test func enrollmentStoreRefusesToClearUnexpectedContent() throws {
    let root = FileManager.default.temporaryDirectory
        .appending(path: "jarvis-enrollment-\(UUID().uuidString)", directoryHint: .isDirectory)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = WakeWordEnrollmentStore(rootURL: root)
    try store.prepare()
    let unexpected = root.appending(path: "jarvis/unexpected.txt")
    try Data([0x01]).write(to: unexpected)
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o600],
        ofItemAtPath: unexpected.path
    )

    #expect(throws: WakeWordEnrollmentError.unsafeStorage) {
        try store.clearSamples()
    }
    #expect(FileManager.default.fileExists(atPath: unexpected.path))
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
