import Foundation
import Testing
@testable import AegisAudioCore

private let previousRevision = String(repeating: "a", count: 40)
private let targetRevision = String(repeating: "b", count: 40)

private func writeUpdateJournal(
    at directory: URL,
    transactionID: UUID,
    state: String,
    installerPID: Int32 = 42,
    extraField: Bool = false
) throws {
    try FileManager.default.createDirectory(
        at: directory,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    var payload: [String: Any] = [
        "schema_version": "1.0",
        "transaction_id": transactionID.uuidString.lowercased(),
        "state": state,
        "previous_revision": previousRevision,
        "target_revision": targetRevision,
        "installer_pid": installerPID,
    ]
    if extraField {
        payload["unexpected"] = true
    }
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    let path = directory.appending(path: "update-transaction.json")
    try data.write(to: path, options: .atomic)
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o600],
        ofItemAtPath: path.path
    )
}

@Test func updateLaunchGuardRejectsNonCanonicalJournalFields() throws {
    let directory = FileManager.default.temporaryDirectory.appending(
        path: "aegis-update-guard-schema-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? FileManager.default.removeItem(at: directory) }
    try writeUpdateJournal(
        at: directory,
        transactionID: UUID(),
        state: "swapped",
        extraField: true
    )

    #expect(throws: SecureUpdateLaunchGuardError.invalidJournal) {
        try SecureUpdateLaunchGuard.authorizeLaunch(
            arguments: ["Jarvis"],
            buildRevision: targetRevision,
            stateDirectory: directory
        )
    }
}

@Test func updateLaunchGuardAllowsOrdinaryLaunchWithoutJournal() throws {
    let directory = FileManager.default.temporaryDirectory.appending(
        path: "aegis-update-guard-empty-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? FileManager.default.removeItem(at: directory) }

    try SecureUpdateLaunchGuard.authorizeLaunch(
        arguments: ["Jarvis"],
        buildRevision: targetRevision,
        stateDirectory: directory
    )
}

@Test func updateLaunchGuardAllowsOnlyTheActiveUpdaterProbe() throws {
    let directory = FileManager.default.temporaryDirectory.appending(
        path: "aegis-update-guard-probe-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? FileManager.default.removeItem(at: directory) }
    let transactionID = UUID()
    try writeUpdateJournal(
        at: directory,
        transactionID: transactionID,
        state: "swapped"
    )

    try SecureUpdateLaunchGuard.authorizeLaunch(
        arguments: ["Jarvis", "--update-probe", transactionID.uuidString],
        buildRevision: targetRevision,
        stateDirectory: directory,
        processIsAlive: { $0 == 42 }
    )
    #expect(throws: SecureUpdateLaunchGuardError.invalidProbe) {
        try SecureUpdateLaunchGuard.authorizeLaunch(
            arguments: ["Jarvis"],
            buildRevision: targetRevision,
            stateDirectory: directory,
            processIsAlive: { _ in true }
        )
    }
}

@Test func updateLaunchGuardRejectsPreparedOrMismatchedBuilds() throws {
    let directory = FileManager.default.temporaryDirectory.appending(
        path: "aegis-update-guard-closed-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    defer { try? FileManager.default.removeItem(at: directory) }
    let transactionID = UUID()
    try writeUpdateJournal(
        at: directory,
        transactionID: transactionID,
        state: "prepared"
    )
    #expect(throws: SecureUpdateLaunchGuardError.uncommittedUpdate) {
        try SecureUpdateLaunchGuard.authorizeLaunch(
            arguments: ["Jarvis"],
            buildRevision: previousRevision,
            stateDirectory: directory
        )
    }

    try writeUpdateJournal(
        at: directory,
        transactionID: transactionID,
        state: "committed"
    )
    #expect(throws: SecureUpdateLaunchGuardError.invalidJournal) {
        try SecureUpdateLaunchGuard.authorizeLaunch(
            arguments: ["Jarvis"],
            buildRevision: previousRevision,
            stateDirectory: directory
        )
    }
}
