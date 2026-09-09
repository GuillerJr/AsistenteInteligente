import Testing
@testable import AegisAudioCore

@Test func wakeWordThermalPolicyPausesOnlyUnderElevatedPressure() {
    #expect(WakeWordThermalPolicy.allowsListening(.nominal))
    #expect(WakeWordThermalPolicy.allowsListening(.fair))
    #expect(!WakeWordThermalPolicy.allowsListening(.serious))
    #expect(!WakeWordThermalPolicy.allowsListening(.critical))
}

@Test func acousticThermalEventsExposeOnlyStableMenuStates() {
    let nominal = AcousticThermalEvent(state: .nominal)
    let fair = AcousticThermalEvent(state: .fair)
    let serious = AcousticThermalEvent(state: .serious)
    let critical = AcousticThermalEvent(state: .critical)

    #expect(nominal.label == "nominal")
    #expect(nominal.allowsListening)
    #expect(fair.label == "fair")
    #expect(fair.allowsListening)
    #expect(serious.label == "serious")
    #expect(!serious.allowsListening)
    #expect(critical.label == "critical")
    #expect(!critical.allowsListening)
}

@Test func wakeWordEnergyPolicyReleasesAudioHardwareInLowPowerMode() {
    #expect(WakeWordEnergyPolicy.allowsListening(lowPowerModeEnabled: false))
    #expect(!WakeWordEnergyPolicy.allowsListening(lowPowerModeEnabled: true))
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

@Test func wakeWordGateRequiresBackgroundBeforeThreeStrongConsecutiveMatches() {
    var gate = WakeWordDecisionGate()

    let backgroundOne = gate.observe(
        keywordIsTopClassification: false,
        confidence: 0.05,
        at: 1
    )
    let backgroundTwo = gate.observe(
        keywordIsTopClassification: false,
        confidence: 0.04,
        at: 1.5
    )
    let backgroundThree = gate.observe(
        keywordIsTopClassification: false,
        confidence: 0.03,
        at: 1.75
    )
    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    let second = gate.observe(keywordIsTopClassification: true, confidence: 0.96, at: 2.5)
    let third = gate.observe(keywordIsTopClassification: true, confidence: 0.97, at: 3)
    #expect(!backgroundOne)
    #expect(!backgroundTwo)
    #expect(!backgroundThree)
    #expect(!first)
    #expect(!second)
    #expect(third)
}

@Test func wakeWordGateRejectsAKeywordSequenceWithoutBackgroundArming() {
    var gate = WakeWordDecisionGate()

    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 1)
    let second = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    #expect(!first)
    #expect(!second)
}

@Test func wakeWordGateResetsAfterBackgroundOrLongGap() {
    var gate = WakeWordDecisionGate()

    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 1)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 1.5)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 1.75)
    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    let background = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 2.5)
    let second = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 3)
    let afterGap = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 5)
    #expect(!first)
    #expect(!background)
    #expect(!second)
    #expect(!afterGap)
}

@Test func wakeWordGateRejectsReplayAndAppliesCooldown() {
    var gate = WakeWordDecisionGate()

    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 1)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 1.5)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.02, at: 1.75)
    let first = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    let replay = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2)
    let second = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 2.5)
    let detection = gate.observe(keywordIsTopClassification: true, confidence: 0.95, at: 3)
    let cooling = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 3)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.01, at: 8)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.01, at: 8.5)
    _ = gate.observe(keywordIsTopClassification: false, confidence: 0.01, at: 8.75)
    let nextFirst = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 9)
    let nextSecond = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 9.5)
    let nextDetection = gate.observe(keywordIsTopClassification: true, confidence: 0.99, at: 10)
    #expect(!first)
    #expect(!replay)
    #expect(!second)
    #expect(detection)
    #expect(!cooling)
    #expect(!nextFirst)
    #expect(!nextSecond)
    #expect(nextDetection)
}

@Test func ownerVerifiedWakeWordRejectsBackgroundNoiseWithoutVoiceActivity() {
    var gate = OwnerVerifiedWakeWordGate(ownerIdentifier: "guillermo")

    _ = gate.observeSpeaker(
        identifier: "guillermo",
        confidence: 0.96,
        runnerUpConfidence: 0.02,
        at: 1
    )
    _ = gate.observeSpeaker(
        identifier: "guillermo",
        confidence: 0.97,
        runnerUpConfidence: 0.01,
        at: 1.2
    )
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1.1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1.2)
    _ = gate.observeWakeWord(keywordIsTopClassification: true, confidence: 0.98, at: 1.3)
    _ = gate.observeWakeWord(keywordIsTopClassification: true, confidence: 0.98, at: 1.4)
    let activation = gate.observeWakeWord(
        keywordIsTopClassification: true,
        confidence: 0.98,
        at: 1.5
    )

    #expect(!activation)
}

@Test func ownerVerifiedWakeWordRejectsAnotherSpeaker() {
    var gate = OwnerVerifiedWakeWordGate(ownerIdentifier: "guillermo")

    gate.observeVoiceActivity(active: true, at: 1)
    _ = gate.observeSpeaker(
        identifier: "visitor",
        confidence: 0.99,
        runnerUpConfidence: 0.01,
        at: 1.05
    )
    _ = gate.observeSpeaker(
        identifier: "visitor",
        confidence: 0.98,
        runnerUpConfidence: 0.02,
        at: 1.15
    )
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1.1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1.2)
    _ = gate.observeWakeWord(keywordIsTopClassification: true, confidence: 0.99, at: 1.3)
    _ = gate.observeWakeWord(keywordIsTopClassification: true, confidence: 0.99, at: 1.4)
    let activation = gate.observeWakeWord(
        keywordIsTopClassification: true,
        confidence: 0.99,
        at: 1.5
    )

    #expect(!activation)
}

@Test func ownerVerifiedWakeWordAcceptsOnlyFreshConcurrentOwnerEvidence() {
    var gate = OwnerVerifiedWakeWordGate(ownerIdentifier: "guillermo")

    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1.1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 1.2)
    gate.observeVoiceActivity(active: true, at: 1.25)
    let firstOwnerObservation = gate.observeSpeaker(
        identifier: "guillermo",
        confidence: 0.94,
        runnerUpConfidence: 0.03,
        at: 1.3
    )
    let firstWakeObservation = gate.observeWakeWord(
        keywordIsTopClassification: true,
        confidence: 0.96,
        at: 1.35
    )
    let secondOwnerObservation = gate.observeSpeaker(
        identifier: "guillermo",
        confidence: 0.95,
        runnerUpConfidence: 0.02,
        at: 1.4
    )
    let secondWakeObservation = gate.observeWakeWord(
        keywordIsTopClassification: true,
        confidence: 0.97,
        at: 1.45
    )
    let activation = gate.observeWakeWord(
        keywordIsTopClassification: true,
        confidence: 0.98,
        at: 1.55
    )

    #expect(!firstOwnerObservation)
    #expect(!firstWakeObservation)
    #expect(!secondOwnerObservation)
    #expect(!secondWakeObservation)
    #expect(activation)
}

@Test func ownerVerifiedWakeWordDoesNotReuseStaleOwnerEvidence() {
    var gate = OwnerVerifiedWakeWordGate(ownerIdentifier: "guillermo")

    gate.observeVoiceActivity(active: true, at: 1)
    _ = gate.observeSpeaker(
        identifier: "guillermo",
        confidence: 0.96,
        runnerUpConfidence: 0.01,
        at: 1.05
    )
    _ = gate.observeSpeaker(
        identifier: "guillermo",
        confidence: 0.97,
        runnerUpConfidence: 0.01,
        at: 1.15
    )
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 2)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 2.1)
    _ = gate.observeWakeWord(keywordIsTopClassification: false, confidence: 0.01, at: 2.2)
    _ = gate.observeWakeWord(keywordIsTopClassification: true, confidence: 0.99, at: 2.3)
    _ = gate.observeWakeWord(keywordIsTopClassification: true, confidence: 0.99, at: 2.4)
    let activation = gate.observeWakeWord(
        keywordIsTopClassification: true,
        confidence: 0.99,
        at: 2.5
    )

    #expect(!activation)
}
