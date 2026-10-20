import AVFoundation
import Foundation
import OSLog
import Speech

private let confirmationLogger = Logger(subsystem: "ai.aegis.menubar", category: "voice-confirmation")

/// Recognizes the owner's short spoken approval ("sí", "aprobado") on-device while the
/// approval window is captured. It streams the same microphone buffers the speaker
/// classifier sees, like ordinary voice turns do; Speech does not reliably finish a
/// single pre-recorded buffer. The daemon still decides whether the words approve.
public final class StreamingConfirmationRecognizer: @unchecked Sendable {
    public static let maximumTranscriptCharacters = 64
    /// The words the daemon accepts as approval. On-device Speech often drops a lone
    /// short word ("sí") as error 1110 (no speech) unless it is told to expect it.
    public static let approvalWords = ["sí", "confirmo", "adelante", "procede", "aprobado",
                                       "dale", "autorizo", "ok"]

    private let lock = NSLock()
    private var appendedFrames: AVAudioFramePosition = 0
    private var sampleRate: Double = 0
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var latest: String?
    private var settled = false

    /// Returns nil when on-device recognition cannot run; the reason is logged.
    public init?(localeIdentifier: String = "es-US") {
        guard SpeechRecognitionPermission.current == .authorized else {
            confirmationLogger.error("confirmation recognizer unavailable reason=permission")
            return nil
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier)) else {
            confirmationLogger.error("confirmation recognizer unavailable reason=locale")
            return nil
        }
        guard recognizer.supportsOnDeviceRecognition else {
            confirmationLogger.error("confirmation recognizer unavailable reason=on_device")
            return nil
        }
        guard recognizer.isAvailable else {
            confirmationLogger.error("confirmation recognizer unavailable reason=busy")
            return nil
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        // Same settings as ordinary voice turns, which recognize reliably, plus the
        // approval vocabulary.
        request.addsPunctuation = true
        request.taskHint = .dictation
        request.contextualStrings = Self.approvalWords
        self.request = request
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            if let result {
                let text = result.bestTranscription.formattedString
                self?.lock.withLock {
                    self?.latest = text
                    if result.isFinal { self?.settled = true }
                }
            } else if let error {
                self?.lock.withLock { self?.settled = true }
                let heard = self?.lock.withLock { () -> Double in
                    guard let self, self.sampleRate > 0 else { return 0 }
                    return Double(self.appendedFrames) / self.sampleRate
                } ?? 0
                confirmationLogger.error(
                    "confirmation recognition failed code=\((error as NSError).code, privacy: .public) audio_seconds=\(heard, privacy: .public)"
                )
            }
        }
    }

    public func append(_ buffer: AVAudioPCMBuffer) {
        let request = lock.withLock { () -> SFSpeechAudioBufferRecognitionRequest? in
            appendedFrames += AVAudioFramePosition(buffer.frameLength)
            sampleRate = buffer.format.sampleRate
            return self.request
        }
        request?.append(buffer)
    }

    /// The text heard so far, bounded like the daemon's contract, or nil when empty.
    public var transcript: String? {
        guard
            let text = lock.withLock({ latest })?.trimmingCharacters(in: .whitespacesAndNewlines),
            (1 ... Self.maximumTranscriptCharacters).contains(text.unicodeScalars.count)
        else { return nil }
        return text
    }

    /// Ends the audio and waits briefly for Speech's final result: a short word is often
    /// only recognized once the stream closes.
    public func endAudioAndWait(seconds: Double = 1.0) async -> String? {
        lock.withLock { request }?.endAudio()
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline, !lock.withLock({ settled }) {
            try? await Task.sleep(for: .milliseconds(50))
        }
        return transcript
    }

    public func finish() {
        let (request, task) = lock.withLock { () -> (SFSpeechAudioBufferRecognitionRequest?, SFSpeechRecognitionTask?) in
            defer { self.request = nil; self.task = nil }
            return (self.request, self.task)
        }
        request?.endAudio()
        task?.cancel()
    }

    deinit { finish() }
}

/// Shared bound for the transcript sent with a voice approval.
public enum OnDeviceConfirmationRecognizer {
    public static let maximumTranscriptCharacters = StreamingConfirmationRecognizer.maximumTranscriptCharacters
}
