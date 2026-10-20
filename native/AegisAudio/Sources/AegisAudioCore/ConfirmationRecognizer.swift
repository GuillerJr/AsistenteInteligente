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

    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var latest: String?

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
        request.addsPunctuation = false
        request.taskHint = .confirmation
        self.request = request
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            if let result {
                let text = result.bestTranscription.formattedString
                self?.lock.withLock { self?.latest = text }
            } else if let error {
                confirmationLogger.error(
                    "confirmation recognition failed code=\((error as NSError).code, privacy: .public)"
                )
            }
        }
    }

    public func append(_ buffer: AVAudioPCMBuffer) {
        lock.withLock { request }?.append(buffer)
    }

    /// The text heard so far, bounded like the daemon's contract, or nil when empty.
    public var transcript: String? {
        guard
            let text = lock.withLock({ latest })?.trimmingCharacters(in: .whitespacesAndNewlines),
            (1 ... Self.maximumTranscriptCharacters).contains(text.unicodeScalars.count)
        else { return nil }
        return text
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
