import Testing
@testable import AegisAudioCore

@Test func wakeWordGateRequiresTwoStrongConsecutiveMatches() {
    var gate = WakeWordDecisionGate()

    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 1)
    let second = gate.observe(keywordIsTopClassification: true, confidence: 0.96, at: 2)
    #expect(!first)
    #expect(second)
}

@Test func wakeWordGateResetsAfterBackgroundOrLongGap() {
    var gate = WakeWordDecisionGate()

    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 1)
    let background = gate.observe(
        keywordIsTopClassification: false,
        confidence: 0.02,
        at: 1.5
    )
    let second = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    let afterGap = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 4)
    #expect(!first)
    #expect(!background)
    #expect(!second)
    #expect(!afterGap)
}

@Test func wakeWordGateRejectsReplayAndAppliesCooldown() {
    var gate = WakeWordDecisionGate()

    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 1)
    let replay = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 1)
    let detection = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    let cooling = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 3)
    let nextFirst = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 7)
    let nextDetection = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 8)
    #expect(!first)
    #expect(!replay)
    #expect(detection)
    #expect(!cooling)
    #expect(!nextFirst)
    #expect(nextDetection)
}
