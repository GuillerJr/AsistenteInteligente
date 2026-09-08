import Foundation

public enum FollowUpListeningAction: Equatable, Sendable {
    case none
    case beginCapture
    case expire
}

/// Monotonic, single-use gate for Jarvis's hands-free follow-up window.
public struct FollowUpListeningGate: Sendable {
    public static let windowSeconds: TimeInterval = 5
    public static let playbackTailSeconds: TimeInterval = 0.45

    private var deadline: TimeInterval?
    private var acceptsSpeechAt: TimeInterval?
    private var readyForSpeech = false

    public init() {}

    public var isArmed: Bool { deadline != nil }

    public mutating func arm(at uptime: TimeInterval, voiceIsActive: Bool) {
        guard uptime.isFinite, uptime >= 0 else {
            cancel()
            return
        }
        deadline = uptime + Self.windowSeconds
        acceptsSpeechAt = uptime + Self.playbackTailSeconds
        // If playback echo is still active, require a quiet transition before
        // accepting a new speech attack. Otherwise the next active transition
        // belongs to the owner, not Jarvis's own loudspeaker tail.
        readyForSpeech = !voiceIsActive
    }

    public mutating func observe(
        voiceIsActive: Bool,
        at uptime: TimeInterval
    ) -> FollowUpListeningAction {
        guard let deadline, uptime.isFinite, uptime >= 0 else { return .none }
        guard uptime < deadline else {
            cancel()
            return .expire
        }
        guard let acceptsSpeechAt, uptime >= acceptsSpeechAt else {
            // Any activity inside the acoustic tail is considered loudspeaker
            // residue. Once observed, a real quiet transition must re-arm the gate.
            if voiceIsActive {
                readyForSpeech = false
            }
            return .none
        }
        guard voiceIsActive else {
            readyForSpeech = true
            return .none
        }
        guard readyForSpeech else { return .none }
        cancel()
        return .beginCapture
    }

    public mutating func expire(at uptime: TimeInterval) -> FollowUpListeningAction {
        guard let deadline, uptime.isFinite, uptime >= deadline else { return .none }
        cancel()
        return .expire
    }

    public mutating func cancel() {
        deadline = nil
        acceptsSpeechAt = nil
        readyForSpeech = false
    }
}
