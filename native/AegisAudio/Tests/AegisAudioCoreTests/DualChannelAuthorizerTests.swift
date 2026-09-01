import Foundation
import Testing
@testable import AegisAudioCore

@Test func adversarialSpeakerThresholdRejectsEverySyntheticVoice() {
    let distractors = Array(repeating: 0.41, count: 15) + [0.72]
    let owners = Array(repeating: 0.91, count: 8)

    let threshold = SpeakerThresholdPolicy.calibratedThreshold(
        distractorConfidences: distractors,
        ownerConfidences: owners
    )

    #expect(threshold == 0.78)
    #expect(distractors.allSatisfy { $0 < threshold! })
    #expect(owners.allSatisfy { $0 >= threshold! })
}

@Test func adversarialSpeakerThresholdFailsClosedForOwnerOverlap() {
    let distractors = Array(repeating: 0.77, count: 15)
    let inconsistentOwnerSamples = [0.94, 0.92, 0.9, 0.88, 0.79]

    #expect(
        SpeakerThresholdPolicy.calibratedThreshold(
            distractorConfidences: distractors,
            ownerConfidences: inconsistentOwnerSamples
        ) == nil
    )
}

@Test func adversarialSpeakerThresholdRequiresCompleteDataset() {
    #expect(
        SpeakerThresholdPolicy.calibratedThreshold(
            distractorConfidences: Array(repeating: 0.1, count: 14),
            ownerConfidences: Array(repeating: 0.9, count: 5)
        ) == nil
    )
}
