import Foundation
import Testing
@testable import AegisAudioCore

@Test func pendingEnrollmentCleanupRemovesOnlyExpiredPrivateCapture() throws {
    let root = temporaryEnrollmentRoot()
    try makePrivateDirectory(root)
    defer { try? FileManager.default.removeItem(at: root) }
    let now = Date(timeIntervalSince1970: 10_000)
    let expired = EnrollmentPendingFiles.makeURL(in: root)
    let active = EnrollmentPendingFiles.makeURL(in: root)
    try makePendingFile(expired, modified: now.addingTimeInterval(-61))
    try makePendingFile(active, modified: now.addingTimeInterval(-59))

    let removed = try EnrollmentPendingFiles.removeAbandoned(in: root, now: now)

    #expect(removed == 1)
    #expect(!FileManager.default.fileExists(atPath: expired.path))
    #expect(FileManager.default.fileExists(atPath: active.path))
}

@Test func pendingEnrollmentCleanupRejectsMalformedOrOpenCapture() throws {
    let malformedRoot = temporaryEnrollmentRoot()
    try makePrivateDirectory(malformedRoot)
    defer { try? FileManager.default.removeItem(at: malformedRoot) }
    let malformed = malformedRoot.appending(path: ".pending-not-a-uuid.caf")
    try makePendingFile(malformed, modified: Date(timeIntervalSince1970: 1))
    #expect(throws: EnrollmentPendingFileError.unsafeFile) {
        try EnrollmentPendingFiles.removeAbandoned(
            in: malformedRoot,
            now: Date(timeIntervalSince1970: 100)
        )
    }

    let openRoot = temporaryEnrollmentRoot()
    try makePrivateDirectory(openRoot)
    defer { try? FileManager.default.removeItem(at: openRoot) }
    let open = EnrollmentPendingFiles.makeURL(in: openRoot)
    try makePendingFile(open, modified: Date(timeIntervalSince1970: 1))
    try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: open.path)
    #expect(throws: EnrollmentPendingFileError.unsafeFile) {
        try EnrollmentPendingFiles.removeAbandoned(
            in: openRoot,
            now: Date(timeIntervalSince1970: 100)
        )
    }
}

@Test func enrollmentStoresCleanExpiredCaptureWithoutTouchingSamples() throws {
    let wakeRoot = temporaryEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: wakeRoot) }
    let wakeStore = WakeWordEnrollmentStore(rootURL: wakeRoot)
    try wakeStore.prepare()
    let wakePending = wakeStore.makeTemporaryURL()
    try makePendingFile(wakePending, modified: Date(timeIntervalSinceNow: -120))

    _ = try wakeStore.progress()

    #expect(!FileManager.default.fileExists(atPath: wakePending.path))

    let speakerRoot = temporaryEnrollmentRoot()
    defer { try? FileManager.default.removeItem(at: speakerRoot) }
    let speakerStore = SpeakerEnrollmentStore(rootURL: speakerRoot)
    try speakerStore.prepare()
    let speakerPending = speakerStore.makeTemporaryURL()
    try makePendingFile(speakerPending, modified: Date(timeIntervalSinceNow: -120))

    _ = try speakerStore.progress()

    #expect(!FileManager.default.fileExists(atPath: speakerPending.path))
}

private func temporaryEnrollmentRoot() -> URL {
    FileManager.default.temporaryDirectory.appending(
        path: "jarvis-pending-enrollment-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
}

private func makePrivateDirectory(_ url: URL) throws {
    try FileManager.default.createDirectory(
        at: url,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
}

private func makePendingFile(_ url: URL, modified: Date) throws {
    try Data([0x63, 0x61, 0x66, 0x66]).write(to: url)
    try FileManager.default.setAttributes(
        [
            .modificationDate: modified,
            .posixPermissions: 0o600,
        ],
        ofItemAtPath: url.path
    )
}
