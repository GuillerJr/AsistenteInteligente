import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

public struct LocalInferenceTurn: Codable, Equatable, Sendable {
    public enum Role: String, Codable, Sendable {
        case user
        case assistant
    }

    public let role: Role
    public let content: String

    public init(role: Role, content: String) {
        self.role = role
        self.content = content
    }
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
public enum LocalInferenceConversation {
    /// Reconstruct only the bounded history supplied for this request. User text
    /// containing "system:" remains text; it never becomes a privileged entry.
    public static func prepare(
        instructions: String,
        turns: [LocalInferenceTurn]
    ) throws -> (transcript: Transcript, prompt: String) {
        guard !instructions.isEmpty,
              !instructions.unicodeScalars.contains(where: { $0.value == 0 }),
              (1...128).contains(turns.count),
              turns.last?.role == .user else {
            throw LocalInferenceHelperError.invalidInput
        }
        var byteCount = instructions.utf8.count
        var merged: [LocalInferenceTurn] = []
        for turn in turns {
            byteCount += turn.content.utf8.count + 2
            guard !turn.content.isEmpty,
                  !turn.content.unicodeScalars.contains(where: { $0.value == 0 }),
                  byteCount <= LocalInferenceHelper.maximumInputBytes else {
                throw LocalInferenceHelperError.invalidInput
            }
            if let previous = merged.last, previous.role == turn.role {
                merged[merged.count - 1] = LocalInferenceTurn(
                    role: turn.role, content: previous.content + "\n\n" + turn.content
                )
            } else {
                merged.append(turn)
            }
        }
        guard let current = merged.popLast() else {
            throw LocalInferenceHelperError.invalidInput
        }
        var entries: [Transcript.Entry] = [
            .instructions(.init(segments: [.text(.init(content: instructions))], toolDefinitions: []))
        ]
        for turn in merged {
            let segments: [Transcript.Segment] = [.text(.init(content: turn.content))]
            switch turn.role {
            case .user:
                entries.append(.prompt(.init(segments: segments)))
            case .assistant:
                entries.append(.response(.init(assetIDs: [], segments: segments)))
            }
        }
        return (Transcript(entries: entries), current.content)
    }
}
#endif
