import Foundation

/// The daemon owns active execution time. The UI allows delivery and cleanup
/// grace after that budget, and never resets its deadline for a partial response.
public struct JobMonitoringDeadline: Sendable {
    public static let deliveryGraceSeconds = 25.0
    private var deadline: TimeInterval
    private var hasServiceBudget = false

    public init(startedAt: TimeInterval) {
        deadline = startedAt + 120 + Self.deliveryGraceSeconds
    }

    public mutating func observe(_ status: IPCJobStatusEvent, at now: TimeInterval) {
        guard let remaining = status.remainingExecutionMilliseconds else { return }
        let candidate = now + Double(remaining) / 1_000 + Self.deliveryGraceSeconds
        deadline = hasServiceBudget ? min(deadline, candidate) : candidate
        hasServiceBudget = true
    }

    public func isExpired(at now: TimeInterval) -> Bool { now >= deadline }
}
