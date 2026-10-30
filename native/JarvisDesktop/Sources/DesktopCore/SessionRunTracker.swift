import Foundation
import Observation

public struct SessionRunObservation: Equatable, Sendable {
    public let scope: SessionScope
    public let runID: String
    public var phase: String
    public var lastSequence: Int
    public var streamingText: String
    public var isActive: Bool {
        ["queued", "running", "waiting_approval", "cancelling", "cleaning", "checking", "disconnected"].contains(phase)
    }
}

/// Presentation custody for concurrent sessions. Selecting another conversation
/// invalidates old UI responses; it sends no cancellation, replay or approval.
@MainActor @Observable public final class SessionRunTracker {
    public private(set) var selectedScope: SessionScope?
    public private(set) var generation = 0
    public private(set) var observations: [SessionScope: SessionRunObservation] = [:]
    public init() {}
    public var active: [SessionRunObservation] { observations.values.filter(\.isActive) }
    public var hasActiveRuns: Bool { observations.values.contains(where: \.isActive) }
    @discardableResult public func select(_ scope: SessionScope?) -> Int {
        if selectedScope != scope { generation += 1; selectedScope = scope }
        return generation
    }
    public func record(scope: SessionScope, runID: String, phase: String) {
        guard ManagementValidation.identifier(scope.sessionID), ManagementValidation.identifier(runID),
              ["queued", "running", "waiting_approval", "cancelling", "cleaning", "checking", "disconnected",
               "completed", "failed", "cancelled", "interrupted"].contains(phase) else { return }
        if var existing = observations[scope], existing.runID == runID {
            // A late pre-settlement read cannot reopen an already settled run.
            if !existing.isActive && SessionRunObservation(scope: scope, runID: runID,
                phase: phase, lastSequence: 0, streamingText: "").isActive { return }
            existing.phase = phase; observations[scope] = existing
        } else {
            observations[scope] = SessionRunObservation(scope: scope, runID: runID,
                phase: phase, lastSequence: 0, streamingText: "")
        }
    }
    public func append(scope: SessionScope, runID: String, sequence: Int, delta: String?) throws {
        guard var value = observations[scope], value.runID == runID, value.isActive,
              sequence > value.lastSequence else { return }
        if let delta { value.streamingText = try RunPresentation.appendDelta(delta, to: value.streamingText) }
        value.lastSequence = sequence; observations[scope] = value
    }
    public func accepts(scope: SessionScope, runID: String, generation: Int) -> Bool {
        self.generation == generation && selectedScope == scope && observations[scope]?.runID == runID
    }
    public func remove(_ scope: SessionScope) { observations.removeValue(forKey: scope) }
    public func reset(retainingDurable: Bool = false) {
        generation += 1; selectedScope = nil
        if retainingDurable {
            observations = observations.filter { !$0.key.temporary }
            for (scope, var value) in observations {
                if value.isActive { value.phase = "disconnected"; observations[scope] = value }
            }
        } else { observations = [:] }
    }
}
