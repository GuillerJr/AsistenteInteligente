import Foundation
import Testing
@testable import AegisAudioCore

@Test func missingSpeakerIdentityModelFailsClosed() {
    #expect(SpeakerIdentityCapability.inspect(modelURL: nil) == .missing)
}

@Test func emptyCompiledSpeakerIdentityDirectoryIsInvalid() throws {
    let modelURL = FileManager.default.temporaryDirectory.appending(
        path: "JarvisSpeakerIdentity-\(UUID().uuidString).mlmodelc",
        directoryHint: .isDirectory
    )
    try FileManager.default.createDirectory(at: modelURL, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: modelURL) }

    #expect(SpeakerIdentityCapability.inspect(modelURL: modelURL) == .invalid)
}

@Test func speakerModelFingerprintIsDeterministicAndContentBound() throws {
    let root = FileManager.default.temporaryDirectory.appending(
        path: "JarvisSpeakerFingerprint-\(UUID().uuidString).mlmodelc",
        directoryHint: .isDirectory
    )
    let nested = root.appending(path: "model", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: nested, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let firstFile = root.appending(path: "coremldata.bin")
    let secondFile = nested.appending(path: "weights.bin")
    try Data("first".utf8).write(to: firstFile)
    try Data("second".utf8).write(to: secondFile)

    let first = SpeakerIdentityCapability.modelFingerprint(at: root)
    let repeated = SpeakerIdentityCapability.modelFingerprint(at: root)
    #expect(first == repeated)
    #expect(first.map(SpeakerIdentityCapability.isValidModelFingerprint) == true)

    try Data("changed".utf8).write(to: secondFile)
    #expect(SpeakerIdentityCapability.modelFingerprint(at: root) != first)
}

@Test func speakerModelFingerprintRejectsSymlinkedContent() throws {
    let root = FileManager.default.temporaryDirectory.appending(
        path: "JarvisSpeakerFingerprint-\(UUID().uuidString).mlmodelc",
        directoryHint: .isDirectory
    )
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: root) }
    try FileManager.default.createSymbolicLink(
        at: root.appending(path: "coremldata.bin"),
        withDestinationURL: URL(fileURLWithPath: "/etc/hosts")
    )

    #expect(SpeakerIdentityCapability.modelFingerprint(at: root) == nil)
}

@Test func privateSpeakerModelRequiresAnOwnerOnlyParent() throws {
    let root = FileManager.default.temporaryDirectory.appending(
        path: "JarvisSpeakerModels-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    let modelURL = root.appending(
        path: SpeakerModelStorage.modelName,
        directoryHint: .isDirectory
    )
    try FileManager.default.createDirectory(
        at: modelURL,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    defer { try? FileManager.default.removeItem(at: root) }

    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root.path)
    #expect(SpeakerModelStorage.secureModelDirectory(for: modelURL) == false)

    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
    #expect(SpeakerModelStorage.secureModelDirectory(for: modelURL) == true)
}

@Test func speakerTrainerRejectsMissingHelperBeforeCreatingModelStorage() throws {
    let root = FileManager.default.temporaryDirectory.appending(
        path: "JarvisSpeakerTraining-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    let modelURL = root.appending(
        path: "Models/\(SpeakerModelStorage.modelName)",
        directoryHint: .isDirectory
    )
    defer { try? FileManager.default.removeItem(at: root) }
    let trainer = SpeakerModelTrainer(
        helperURL: root.appending(path: "missing-helper"),
        datasetURL: root.appending(path: "dataset", directoryHint: .isDirectory),
        modelURL: modelURL
    )

    #expect(throws: SpeakerModelTrainingError.helperUnavailable) {
        try trainer.train()
    }
    #expect(FileManager.default.fileExists(atPath: modelURL.deletingLastPathComponent().path) == false)
}

@Test func speakerTrainerRejectsSymlinkedHelper() throws {
    let root = FileManager.default.temporaryDirectory.appending(
        path: "JarvisSpeakerHelper-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: root) }
    let helperURL = root.appending(path: "jarvis-speaker-trainer")
    try FileManager.default.createSymbolicLink(
        at: helperURL,
        withDestinationURL: URL(fileURLWithPath: "/usr/bin/true")
    )
    let modelURL = root.appending(
        path: "Models/\(SpeakerModelStorage.modelName)",
        directoryHint: .isDirectory
    )
    let trainer = SpeakerModelTrainer(
        helperURL: helperURL,
        datasetURL: root.appending(path: "dataset", directoryHint: .isDirectory),
        modelURL: modelURL
    )

    #expect(throws: SpeakerModelTrainingError.helperUnavailable) {
        try trainer.train()
    }
    #expect(FileManager.default.fileExists(atPath: modelURL.deletingLastPathComponent().path) == false)
}

@Test func speakerIdentityRequiresRepeatedHighMarginEvidence() {
    var gate = SpeakerIdentityDecisionGate()
    gate.observe(identifier: "guillermo", confidence: 0.9, runnerUpConfidence: 0.5)
    #expect(gate.result() == nil)
    gate.observe(identifier: "guillermo", confidence: 0.86, runnerUpConfidence: 0.6)

    #expect(gate.result()?.identifier == "guillermo")
    #expect(gate.result()?.confidence == 0.88)
}

@Test func speakerIdentityRejectsLowMarginAndReservedLabels() {
    var gate = SpeakerIdentityDecisionGate()
    gate.observe(identifier: "background", confidence: 0.98, runnerUpConfidence: 0.01)
    gate.observe(identifier: "visitor", confidence: 0.88, runnerUpConfidence: 0.82)
    gate.observe(identifier: "visitor", confidence: 0.9, runnerUpConfidence: 0.84)

    #expect(gate.result() == nil)
}

@Test func speakerTurnAdmissionRejectsAnUntrustedOrAmbiguousTurn() throws {
    let owner = try #require(SpeakerIdentityResult(identifier: "guillermo", confidence: 0.91))
    let visitor = try #require(SpeakerIdentityResult(identifier: "visitor", confidence: 0.99))
    let weakOwner = try #require(SpeakerIdentityResult(identifier: "guillermo", confidence: 0.77))

    #expect(SpeakerTurnAdmissionPolicy.accepts(
        owner,
        selectedOwnerIdentifier: "guillermo",
        resolvedOwnerIdentifier: "guillermo"
    ))
    #expect(!SpeakerTurnAdmissionPolicy.accepts(
        visitor,
        selectedOwnerIdentifier: "guillermo",
        resolvedOwnerIdentifier: "guillermo"
    ))
    #expect(!SpeakerTurnAdmissionPolicy.accepts(
        weakOwner,
        selectedOwnerIdentifier: "guillermo",
        resolvedOwnerIdentifier: "guillermo"
    ))
    #expect(!SpeakerTurnAdmissionPolicy.accepts(
        owner,
        selectedOwnerIdentifier: "guillermo",
        resolvedOwnerIdentifier: nil
    ))
}

@Test func speakerOwnerPolicyUsesOneProfileOrAnExplicitActiveSelection() {
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo"],
            selectedIdentifier: nil
        ) == "guillermo"
    )
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo", "invitado"],
            selectedIdentifier: nil
        ) == nil
    )
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo", "invitado"],
            selectedIdentifier: "guillermo"
        ) == "guillermo"
    )
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo"],
            selectedIdentifier: "perfil_viejo"
        ) == nil
    )
}

@Test func explicitSpeakerOwnerSelectionIsBoundToOneModelFingerprint() {
    let modelA = String(repeating: "a", count: 64)
    let modelB = String(repeating: "b", count: 64)
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo", "invitado"],
            selectedIdentifier: "guillermo",
            selectedModelFingerprint: modelA,
            activeModelFingerprint: modelA
        ) == "guillermo"
    )
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo", "invitado"],
            selectedIdentifier: "guillermo",
            selectedModelFingerprint: modelA,
            activeModelFingerprint: modelB
        ) == nil
    )
    #expect(
        SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: ["guillermo"],
            selectedIdentifier: nil,
            selectedModelFingerprint: nil,
            activeModelFingerprint: modelB
        ) == "guillermo"
    )
}
