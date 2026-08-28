import Testing
@testable import AegisAudioCore

@Test func adaptiveNoiseFloorUsesSpecifiedDownwardEMA() {
    var tracker = AdaptiveNoiseFloorTracker(initialNoiseFloor: 0.004)

    let transition = tracker.observe(rms: 0.002)

    #expect(transition == nil)
    #expect(abs(tracker.noiseFloor - 0.0039) < 0.000_001)
    #expect(abs(tracker.activeThreshold - tracker.noiseFloor * 2.5) < 0.000_001)
}

@Test func adaptiveNoiseFloorRequiresTemporalAttackAndRelease() throws {
    var tracker = AdaptiveNoiseFloorTracker(initialNoiseFloor: 0.004)

    #expect(tracker.observe(rms: 0.020) == nil)
    #expect(tracker.observe(rms: 0.020) == nil)
    let activeEvent = tracker.observe(rms: 0.020)
    let active = try #require(activeEvent)
    #expect(active.voiceActive)

    for _ in 0 ..< 7 {
        #expect(tracker.observe(rms: 0.000_1) == nil)
    }
    let inactiveEvent = tracker.observe(rms: 0.000_1)
    let inactive = try #require(inactiveEvent)
    #expect(!inactive.voiceActive)
}

@Test func adaptiveNoiseFloorEventuallyAdaptsToSustainedOfficeNoise() {
    var tracker = AdaptiveNoiseFloorTracker(initialNoiseFloor: 0.004)

    for _ in 0 ..< 500 {
        _ = tracker.observe(rms: 0.012)
    }

    #expect(tracker.noiseFloor > 0.004)
    #expect(!tracker.voiceActive)
    #expect(tracker.activeThreshold > 0.012)
}

@Test func adaptiveNoiseFloorRejectsClicksAndNonFiniteSamples() {
    var tracker = AdaptiveNoiseFloorTracker(initialNoiseFloor: 0.004)

    #expect(tracker.observe(rms: 0.020) == nil)
    #expect(tracker.observe(rms: .nan) == nil)
    #expect(tracker.observe(rms: 0.020) == nil)
    #expect(tracker.observe(rms: 0.020) == nil)
    #expect(tracker.observe(rms: 0.001) == nil)
    #expect(!tracker.voiceActive)
    #expect(tracker.noiseFloor > 0)
}
