import Foundation

public struct ProactiveAlertGate: Sendable {
    private var lastEmission: [String: Date] = [:]

    public init() {}

    public mutating func shouldEmit(
        key: String,
        now: Date = Date(),
        cooldown: TimeInterval
    ) -> Bool {
        guard
            !key.isEmpty,
            key.count <= 256,
            cooldown.isFinite,
            cooldown >= 0
        else {
            return false
        }
        if let previous = lastEmission[key], now.timeIntervalSince(previous) < cooldown {
            return false
        }
        lastEmission[key] = now
        if lastEmission.count > 128 {
            lastEmission = Dictionary(
                uniqueKeysWithValues: lastEmission
                    .sorted(by: { $0.value < $1.value })
                    .suffix(128)
            )
        }
        return true
    }

    public mutating func reset() {
        lastEmission.removeAll(keepingCapacity: false)
    }
}
