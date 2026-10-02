import Foundation

/// One critically damped motion drives the panel and everything inside it.
/// Retargeting preserves position and velocity, including during rapid reversals.
public struct NotchExpansionMotion: Sendable {
    public private(set) var progress: Double = 0
    public private(set) var velocity: Double = 0
    public private(set) var target: Double = 0

    public var isMoving: Bool { progress != target || velocity != 0 }

    public init() {}

    public mutating func retarget(expanded: Bool) {
        target = expanded ? 1 : 0
    }

    public mutating func finish(expanded: Bool) {
        target = expanded ? 1 : 0
        progress = target
        velocity = 0
    }

    public mutating func advance(by seconds: Double) {
        guard isMoving, seconds.isFinite, seconds > 0 else { return }
        let frequency = target == 1 ? 24.0 : 28.0
        let displacement = progress - target
        let coefficient = velocity + frequency * displacement
        let decay = exp(-frequency * seconds)
        let next = target + (displacement + coefficient * seconds) * decay
        velocity = (velocity - frequency * coefficient * seconds) * decay
        progress = min(max(next, 0), 1)
        // A reversal can carry momentum toward an endpoint briefly. Never let
        // that momentum enlarge the window beyond its safe presentation sizes.
        if next != progress { velocity = 0 }
        if abs(progress - target) < 0.0005 && abs(velocity) < 0.015 {
            progress = target
            velocity = 0
        }
    }
}
