import AegisAudioCore
import Foundation
import Observation

@MainActor @Observable
final class JarvisChatStore {
    var transcript = TextChatTranscript()
    var conversationID: UUID?
    var inputJobID: UUID?
    var submitting = false
    var cancelling = false
    var isBusy: Bool { submitting || transcript.jobID != nil }

    func newConversation() {
        guard !isBusy else { return }
        transcript = TextChatTranscript()
        conversationID = nil
        inputJobID = nil
    }
}
