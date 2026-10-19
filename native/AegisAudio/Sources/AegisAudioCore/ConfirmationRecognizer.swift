import AVFoundation
import Foundation
import Speech

/// Recognizes the owner's short spoken approval ("sí", "aprobado") from the same
/// 16 kHz PCM window that is signed and sent to the daemon. Recognition stays
/// on-device; the daemon still decides whether the words approve the action.
public final class OnDeviceConfirmationRecognizer: @unchecked Sendable {
    public static let maximumTranscriptCharacters = 64

    private let localeIdentifier: String
    private let timeout: Duration

    public init(localeIdentifier: String = "es-US", timeout: Duration = .milliseconds(2_500)) {
        self.localeIdentifier = localeIdentifier
        self.timeout = timeout
    }

    /// Returns nil whenever recognition is unavailable, empty or oversized, so the
    /// caller falls back to sending the audio alone.
    public func transcribe(pcmS16LE: Data) async -> String? {
        guard
            SpeechRecognitionPermission.current == .authorized,
            pcmS16LE.count >= 2,
            pcmS16LE.count.isMultiple(of: 2),
            let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier)),
            recognizer.supportsOnDeviceRecognition,
            recognizer.isAvailable,
            let buffer = Self.buffer(from: pcmS16LE)
        else {
            return nil
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false
        request.addsPunctuation = false
        request.taskHint = .confirmation
        request.append(buffer)
        request.endAudio()

        let outcome = ConfirmationOutcome()
        let task = recognizer.recognitionTask(with: request) { result, error in
            if let result, result.isFinal {
                outcome.finish(result.bestTranscription.formattedString)
            } else if error != nil {
                outcome.finish(nil)
            }
        }
        let text = await outcome.wait(timeout: timeout)
        task.cancel()
        guard
            let text = text?.trimmingCharacters(in: .whitespacesAndNewlines),
            (1 ... Self.maximumTranscriptCharacters).contains(text.unicodeScalars.count)
        else {
            return nil
        }
        return text
    }

    static func buffer(from pcmS16LE: Data) -> AVAudioPCMBuffer? {
        let frames = pcmS16LE.count / 2
        guard
            let format = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: 16_000,
                channels: 1,
                interleaved: false
            ),
            let buffer = AVAudioPCMBuffer(
                pcmFormat: format,
                frameCapacity: AVAudioFrameCount(frames)
            ),
            let channel = buffer.floatChannelData?[0]
        else {
            return nil
        }
        pcmS16LE.withUnsafeBytes { raw in
            for index in 0 ..< frames {
                let sample = raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self)
                channel[index] = Float(Int16(littleEndian: sample)) / 32_768
            }
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        return buffer
    }
}

/// Resolves exactly once: from the recognizer callback or from the timeout.
private final class ConfirmationOutcome: @unchecked Sendable {
    private let lock = NSLock()
    private var value: String??
    private var continuation: CheckedContinuation<String?, Never>?

    func finish(_ text: String?) {
        lock.lock()
        guard value == nil else {
            lock.unlock()
            return
        }
        value = .some(text)
        let waiting = continuation
        continuation = nil
        lock.unlock()
        waiting?.resume(returning: text)
    }

    func wait(timeout: Duration) async -> String? {
        let timer = Task { [weak self] in
            try? await Task.sleep(for: timeout)
            self?.finish(nil)
        }
        defer { timer.cancel() }
        return await withCheckedContinuation { continuation in
            lock.lock()
            if let value {
                lock.unlock()
                continuation.resume(returning: value)
                return
            }
            self.continuation = continuation
            lock.unlock()
        }
    }
}
