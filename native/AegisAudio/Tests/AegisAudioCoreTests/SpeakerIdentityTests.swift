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
