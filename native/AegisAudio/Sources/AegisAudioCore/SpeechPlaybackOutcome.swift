import Foundation

/// A completed language-model response is not proof that audio was played.
public enum SpeechPlaybackOutcome: Equatable, Sendable {
    case completed
    case unavailable
    case interrupted
    case localReplayUnavailable
    case invalidText

    public var failureCode: String? {
        switch self {
        case .completed: nil
        case .unavailable: "speech_output_unavailable"
        case .interrupted: "speech_output_interrupted"
        case .localReplayUnavailable: "speech_replay_unavailable"
        case .invalidText: "speech_text_invalid"
        }
    }
}
