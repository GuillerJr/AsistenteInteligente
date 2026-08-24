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
