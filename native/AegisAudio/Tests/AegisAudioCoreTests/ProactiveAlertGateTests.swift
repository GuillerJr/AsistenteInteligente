import Foundation
import Testing
@testable import AegisAudioCore

@Test func proactiveGateDeduplicatesInsideCooldown() {
    var gate = ProactiveAlertGate()
    let start = Date(timeIntervalSince1970: 1_000)

    let first = gate.shouldEmit(key: "battery-low", now: start, cooldown: 300)
    let duplicate = gate.shouldEmit(
        key: "battery-low",
        now: start.addingTimeInterval(299),
        cooldown: 300
    )
    let afterCooldown = gate.shouldEmit(
        key: "battery-low",
        now: start.addingTimeInterval(300),
        cooldown: 300
    )

    #expect(first)
    #expect(!duplicate)
    #expect(afterCooldown)
}

@Test func proactiveGateKeepsIndependentEventKeys() {
    var gate = ProactiveAlertGate()
    let now = Date(timeIntervalSince1970: 2_000)

    let offline = gate.shouldEmit(key: "network-offline", now: now, cooldown: 600)
    let restored = gate.shouldEmit(key: "network-restored", now: now, cooldown: 600)
    let empty = gate.shouldEmit(key: "", now: now, cooldown: 600)
    let invalidCooldown = gate.shouldEmit(
        key: "thermal",
        now: now,
        cooldown: -Double.infinity
    )

    #expect(offline)
    #expect(restored)
    #expect(!empty)
    #expect(!invalidCooldown)
}
