import Testing
@testable import AegisAudioCore

@Test func wakeWordThermalPolicyPausesOnlyUnderElevatedPressure() {
    #expect(WakeWordThermalPolicy.allowsListening(.nominal))
    #expect(WakeWordThermalPolicy.allowsListening(.fair))
    #expect(!WakeWordThermalPolicy.allowsListening(.serious))
    #expect(!WakeWordThermalPolicy.allowsListening(.critical))
}

@Test func wakeWordAvailabilityPolicyFollowsOnlyRealTransitions() {
    let becameAvailable = WakeWordAvailabilityPolicy.action(
        previousAvailable: false,
        currentAvailable: true,
        active: true,
        startEligible: true,
        detectorRunning: false
    )
    let stableFailure = WakeWordAvailabilityPolicy.action(
        previousAvailable: true,
        currentAvailable: true,
        active: true,
        startEligible: true,
        detectorRunning: false
    )
    let becameUnavailable = WakeWordAvailabilityPolicy.action(
        previousAvailable: true,
        currentAvailable: false,
        active: true,
        startEligible: true,
        detectorRunning: true
    )
    let unavailableIdle = WakeWordAvailabilityPolicy.action(
        previousAvailable: false,
        currentAvailable: false,
        active: true,
        startEligible: true,
        detectorRunning: false
    )
    let ineligible = WakeWordAvailabilityPolicy.action(
        previousAvailable: false,
        currentAvailable: true,
        active: true,
        startEligible: false,
        detectorRunning: false
    )
    let inactiveTransition = WakeWordAvailabilityPolicy.action(
        previousAvailable: true,
        currentAvailable: false,
        active: false,
        startEligible: false,
        detectorRunning: false
    )
    let inactiveResidualDetector = WakeWordAvailabilityPolicy.action(
        previousAvailable: true,
        currentAvailable: false,
        active: false,
        startEligible: false,
        detectorRunning: true
    )

    #expect(becameAvailable == .start)
    #expect(stableFailure == .none)
    #expect(becameUnavailable == .stop)
    #expect(unavailableIdle == .none)
    #expect(ineligible == .none)
    #expect(inactiveTransition == .none)
    #expect(inactiveResidualDetector == .stop)
}

@Test func wakeWordRecoveryAllowsOneRetryUntilStableReset() {
    var gate = WakeWordRecoveryGate()

    let first = gate.consumeRetry()
    let repeated = gate.consumeRetry()
    gate.reset()
    let afterReset = gate.consumeRetry()
    #expect(first)
    #expect(!repeated)
    #expect(afterReset)
}

@Test func wakeWordResumeRequiresContinuousAcousticSettleTime() {
    var gate = WakeWordResumeGate()

    let started = gate.observe(audioIsBusy: false, at: 1)
    let early = gate.observe(audioIsBusy: false, at: 1.74)
    let settled = gate.observe(audioIsBusy: false, at: 1.75)
    #expect(!started)
    #expect(!early)
    #expect(settled)
}

@Test func wakeWordResumeSettleTimeRestartsAfterAudioActivity() {
    var gate = WakeWordResumeGate()

    let firstQuiet = gate.observe(audioIsBusy: false, at: 1)
    let interrupted = gate.observe(audioIsBusy: true, at: 1.5)
    let secondQuiet = gate.observe(audioIsBusy: false, at: 2)
    let early = gate.observe(audioIsBusy: false, at: 2.7)
    let settled = gate.observe(audioIsBusy: false, at: 2.75)
    #expect(!firstQuiet)
    #expect(!interrupted)
    #expect(!secondQuiet)
    #expect(!early)
    #expect(settled)
}

@Test func wakeWordResumeRejectsReplayedTime() {
    var gate = WakeWordResumeGate()

    let started = gate.observe(audioIsBusy: false, at: 1)
    let replayed = gate.observe(audioIsBusy: false, at: 1)
    let settled = gate.observe(audioIsBusy: false, at: 1.75)
    #expect(!started)
    #expect(!replayed)
    #expect(settled)
}

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
