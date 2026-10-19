import Foundation

/// A deterministic, local behavior model. It never starts jobs or invents task outcomes.
public struct MascotBehaviorState: Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        case resting, sleeping, curious, greeting, acknowledging
        case listening, thinking, speaking, attention, celebrating, paused
    }

    public struct Pose: Equatable, Sendable {
        public let phase: Phase
        public let gazeX: Double
        public let gazeY: Double
        public let tilt: Double
        public let nod: Double
        public let eyeOpenness: Double
        public let affection: Double
    }

    public private(set) var isHovered = false
    public private(set) var contactCount = 0
    private var activity: AssistantPresentation.Kind = .idle
    private var available = true
    private var notice = false
    private var lastEvent: Double
    private var lastActivity: Double
    private var contactAt = -Double.infinity
    private var greetingAt = -Double.infinity
    private var completedAt = -Double.infinity
    private var gazeAt: Double
    private var gazeFromX = 0.0
    private var gazeFromY = 0.0
    private var gazeToX = 0.0
    private var gazeToY = 0.0
    private var autonomousIntent: MascotIntent?

    public init(now: Double = 0) {
        let time = now.isFinite ? now : 0
        lastEvent = time
        lastActivity = time
        gazeAt = time
    }

    public mutating func update(kind: AssistantPresentation.Kind, sessionAvailable: Bool,
                                hasNotice: Bool = false, at time: Double) {
        guard let time = accept(time) else { return }
        if !sessionAvailable {
            // A locked session retains no pointer, contact or completion memory.
            self = Self(now: time)
            available = false
            return
        }
        if !available { lastActivity = time }
        available = true
        notice = hasNotice
        guard activity != kind else { return }
        if kind == .completed && [.submitting, .processing, .working, .speaking].contains(activity) {
            completedAt = time
        } else if kind != .completed {
            completedAt = -.infinity
        }
        activity = kind
        lastActivity = time
    }

    public mutating func hover(_ hovered: Bool, at time: Double) {
        guard available, let time = accept(time), hovered != isHovered else { return }
        if hovered && pose(at: time).phase == .sleeping { greetingAt = time }
        isHovered = hovered
        lastActivity = time
        if !hovered { movePointer(x: 0, y: 0, at: time) }
    }

    /// Normalized coordinates in the mascot's own panel, never the desktop.
    public mutating func movePointer(x: Double, y: Double, at time: Double) {
        guard available, x.isFinite, y.isFinite, let time = accept(time) else { return }
        let gaze = gaze(at: time)
        gazeFromX = gaze.0
        gazeFromY = gaze.1
        gazeToX = min(max(x, -1), 1)
        gazeToY = min(max(y, -1), 1)
        gazeAt = time
    }

    public mutating func touch(at time: Double) {
        guard available, let time = accept(time), time - contactAt >= 0.45 else { return }
        contactCount = min(contactCount + 1, 8)
        contactAt = time
        lastActivity = time
    }

    public mutating func setAutonomousIntent(_ intent: MascotIntent?) {
        autonomousIntent = intent
    }

    public func pose(at requestedTime: Double, reducedMotion: Bool = false) -> Pose {
        let time = requestedTime.isFinite ? max(requestedTime, lastEvent) : lastEvent
        let phase: Phase
        if !available { phase = .paused }
        else if isAttention { phase = .attention }
        else if let working = workingPhase { phase = working }
        else if notice { phase = .attention }
        else if time - completedAt < 2.6 { phase = .celebrating }
        else if time - contactAt < 1.4 { phase = .acknowledging }
        else if time - greetingAt < 1.5 { phase = .greeting }
        else if isHovered { phase = .curious }
        else if autonomousIntent == .rest { phase = .sleeping }
        else if autonomousIntent == .explore || autonomousIntent == .approach { phase = .curious }
        else if time - lastActivity >= 75 { phase = .sleeping }
        else { phase = .resting }

        let gaze = gaze(at: time)
        let followsPointer = [.curious, .greeting, .acknowledging, .resting].contains(phase)
        let touchAge = time - contactAt
        let affectionate = phase == .acknowledging
            ? (reducedMotion ? 0.5 : min(max(touchAge / 0.12, 0), 1) * max(0, 1 - touchAge / 1.4)) : 0
        let reactionAge = phase == .celebrating ? time - completedAt : time - greetingAt
        let wave = reactionAge.isFinite ? max(0, 1 - reactionAge / 1.5) * sin(max(0, reactionAge) * 6) : 0
        let nod = phase == .acknowledging
            ? sin(max(0, touchAge) * 7) * affectionate * (1.8 + Double(contactCount) * 0.15)
            : ([.celebrating, .greeting].contains(phase) ? wave * 2 : 0)
        let sleepiness = phase == .sleeping ? 1 :
            (!isHovered && autonomousIntent == nil && workingPhase == nil && !isAttention && !notice
             ? min(max((time - lastActivity - 70) / 5, 0), 1) : 0)
        let waking = phase == .greeting ? min(max((time - greetingAt) / 0.35, 0), 1) : 1
        let eyes = phase == .paused ? 0.12
            : (1 - sleepiness * 0.88) * (0.12 + waking * 0.88) * (1 - affectionate * 0.45)
        return Pose(phase: phase,
                    gazeX: followsPointer ? gaze.0 * 0.025 : 0,
                    gazeY: followsPointer ? gaze.1 * 0.015 : 0,
                    tilt: reducedMotion || !followsPointer ? 0 : gaze.0 * 0.045,
                    nod: reducedMotion ? 0 : nod,
                    eyeOpenness: eyes,
                    affection: affectionate)
    }

    private var isAttention: Bool {
        [.approval, .browserSelection, .failure, .securityBlocked, .offline,
         .securityUnavailable, .brainMissing, .brainUnavailable].contains(activity)
    }

    private var workingPhase: Phase? {
        switch activity {
        case .listening, .followingUp: .listening
        case .processing, .submitting, .working, .connecting, .securityChecking, .brainChecking, .interrupting: .thinking
        case .speaking: .speaking
        default: nil
        }
    }

    private func gaze(at time: Double) -> (Double, Double) {
        let amount = 1 - exp(-max(0, time - gazeAt) / 0.10)
        return (gazeFromX + (gazeToX - gazeFromX) * amount,
                gazeFromY + (gazeToY - gazeFromY) * amount)
    }

    private mutating func accept(_ time: Double) -> Double? {
        guard time.isFinite else { return nil }
        lastEvent = max(time, lastEvent)
        return lastEvent
    }
}
