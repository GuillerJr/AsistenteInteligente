import Foundation
import Testing
@testable import AegisAudioCore

@Test func speakerEnrollmentGuidanceRotatesCaptureConditions() {
    #expect(
        SpeakerEnrollmentGuidance.instruction(
            target: .speaker("guillermo"),
            acceptedCount: 0
        ) == "Habla con voz normal y una frase natural"
    )
    #expect(
        SpeakerEnrollmentGuidance.instruction(
            target: .background,
            acceptedCount: 10
        ) == "Captura una voz que no esté enrolada"
    )
}

@Test func speakerEnrollmentStoreCreatesPrivateDatasetAndProfiles() throws {
    let root = temporarySpeakerEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = SpeakerEnrollmentStore(rootURL: root)

    _ = try store.addProfile("guillermo")
    let progress = try store.addProfile("invitado")

    #expect(progress.profiles.map(\.identifier) == ["guillermo", "invitado"])
    #expect(progress.backgroundCount == 0)
    #expect(!progress.isReady)
    for directory in [
        root,
        root.appending(path: "background"),
        root.appending(path: "guillermo"),
        root.appending(path: "invitado"),
    ] {
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.path)
        #expect(attributes[.posixPermissions] as? Int == 0o700)
    }
}

@Test func speakerEnrollmentStoreCommitsPrivateSamplesAndBecomesReady() throws {
    let root = temporarySpeakerEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = SpeakerEnrollmentStore(rootURL: root)
    _ = try store.addProfile("guillermo")
    _ = try store.addProfile("invitado")

    for target in [
        SpeakerEnrollmentTarget.background,
        .speaker("guillermo"),
        .speaker("invitado"),
    ] {
        for _ in 0 ..< SpeakerEnrollmentProgress.targetPerLabel {
            try commitFakeSpeakerSample(store: store, target: target)
        }
    }

    let progress = try store.progress()
    #expect(progress.isReady)
    #expect(progress.backgroundCount == SpeakerEnrollmentProgress.targetPerLabel)
    #expect(progress.profiles.allSatisfy {
        $0.sampleCount == SpeakerEnrollmentProgress.targetPerLabel
    })
    let sample = try #require(
        FileManager.default.contentsOfDirectory(
            at: root.appending(path: "guillermo"),
            includingPropertiesForKeys: nil
        ).first
    )
    let attributes = try FileManager.default.attributesOfItem(atPath: sample.path)
    #expect(attributes[.posixPermissions] as? Int == 0o600)
}

@Test func speakerEnrollmentRejectsUnsafeAndDuplicateProfiles() throws {
    let root = temporarySpeakerEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = SpeakerEnrollmentStore(rootURL: root)

    #expect(throws: SpeakerEnrollmentError.invalidIdentifier) {
        try store.addProfile("../owner")
    }
    _ = try store.addProfile("owner")
    #expect(throws: SpeakerEnrollmentError.duplicateProfile) {
        try store.addProfile("owner")
    }
    #expect(throws: SpeakerEnrollmentError.missingProfile) {
        try store.validate(.speaker("visitor"))
    }
}

@Test func speakerEnrollmentRemovalIsScopedAndExplicit() throws {
    let root = temporarySpeakerEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let store = SpeakerEnrollmentStore(rootURL: root)
    _ = try store.addProfile("owner")
    _ = try store.addProfile("guest")
    try commitFakeSpeakerSample(store: store, target: .speaker("owner"))
    try commitFakeSpeakerSample(store: store, target: .speaker("guest"))

    let progress = try store.removeProfile("guest")

    #expect(progress.profiles.map(\.identifier) == ["owner"])
    #expect(FileManager.default.fileExists(atPath: root.appending(path: "owner").path))
    #expect(!FileManager.default.fileExists(atPath: root.appending(path: "guest").path))
}

@Test func speakerEnrollmentRefusesUnexpectedOrSymlinkedStorage() throws {
    let unexpectedRoot = temporarySpeakerEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: unexpectedRoot) }
    let unexpectedStore = SpeakerEnrollmentStore(rootURL: unexpectedRoot)
    try unexpectedStore.prepare()
    try Data([0x01]).write(to: unexpectedRoot.appending(path: "unexpected.txt"))
    #expect(throws: SpeakerEnrollmentError.unsafeStorage) {
        try unexpectedStore.progress()
    }

    let container = temporarySpeakerEnrollmentRoot()
    let actual = container.appending(path: "actual", directoryHint: .isDirectory)
    let link = container.appending(path: "link", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: actual, withIntermediateDirectories: true)
    try FileManager.default.createSymbolicLink(at: link, withDestinationURL: actual)
    defer { try? FileManager.default.removeItem(at: container) }
    #expect(throws: SpeakerEnrollmentError.unsafeStorage) {
        try SpeakerEnrollmentStore(rootURL: link).prepare()
    }
}

private func temporarySpeakerEnrollmentRoot() -> URL {
    FileManager.default.temporaryDirectory.appending(
        path: "jarvis-speaker-enrollment-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
}

private func commitFakeSpeakerSample(
    store: SpeakerEnrollmentStore,
    target: SpeakerEnrollmentTarget
) throws {
    let temporary = store.makeTemporaryURL()
    try Data([0x63, 0x61, 0x66, 0x66]).write(to: temporary)
    try store.commit(temporary, target: target)
}
