import Foundation

/// Dictation may add reviewed text only to the still-selected originating
/// conversation. It never submits a message or replaces an existing draft.
public enum LocalVoiceDraftBridge {
    public static func contextID(_ scope: SessionScope, runtimeID: UUID? = nil) -> String {
        (runtimeID.map { $0.uuidString + ":" } ?? "") + (scope.temporary ? "temporary:" : "persistent:") + scope.sessionID
    }

    public static func append(_ transcript: LocalVoiceTranscript, scope: SessionScope?, existing: String,
                              eligible: Bool, maximumBytes: Int = 32000, runtimeID: UUID? = nil) -> String? {
        guard eligible, let scope, transcript.contextID == contextID(scope, runtimeID: runtimeID), transcript.requiresReview,
              transcript.source == "on_device_speech", !transcript.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              transcript.text.utf8.count <= 16000, !transcript.text.contains("\0") else { return nil }
        let result = existing.isEmpty ? transcript.text : existing + (existing.hasSuffix("\n") ? "" : "\n") + transcript.text
        guard maximumBytes > 0, result.utf8.count <= maximumBytes else { return nil }
        return result
    }
}
