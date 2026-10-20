@preconcurrency import AVFoundation
import Foundation
import OSLog
import Speech

private let meetingLogger = Logger(subsystem: "ai.aegis.menubar", category: "meeting-notes")

/// Long on-device transcription for meeting notes (Codex's Meetings). The audio never
/// leaves the Mac: Speech runs on-device and only the text is kept. A recognition task
/// is rotated every segment so a long meeting is not cut off by the task's own limits;
/// each finished segment is kept in order.
public final class MeetingTranscriber: @unchecked Sendable {
    public static let segmentSeconds: TimeInterval = 50
    /// Four 32 KB documents is what one request can carry (about two hours of speech).
    public static let maximumCharacters = 120_000

    private let engine = AVAudioEngine()
    private let recognizer: SFSpeechRecognizer
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var segments: [String] = []
    private var partial = ""
    private var rotation: Timer?
    private var finishing: [SFSpeechRecognitionTask] = []

    /// Nil when on-device recognition cannot run (permission, locale or device).
    public init?(localeIdentifier: String = "es-US") {
        guard SpeechRecognitionPermission.current == .authorized,
              MicrophonePermission.current == .authorized,
              let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier)),
              recognizer.supportsOnDeviceRecognition, recognizer.isAvailable
        else {
            meetingLogger.error("meeting_transcriber_unavailable")
            return nil
        }
        self.recognizer = recognizer
    }

    /// Everything heard so far, for the live preview.
    public var text: String {
        lock.withLock { (segments + [partial]).filter { !$0.isEmpty }.joined(separator: " ") }
    }

    public func start() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 4_096, format: format) { [weak self] buffer, _ in
            self?.lock.withLock { self?.request }?.append(buffer)
        }
        beginSegment()
        engine.prepare()
        try engine.start()
        let timer = Timer(timeInterval: Self.segmentSeconds, repeats: true) { [weak self] _ in
            self?.beginSegment()
        }
        RunLoop.main.add(timer, forMode: .common)
        rotation = timer
        meetingLogger.info("meeting_transcription_started")
    }

    /// Stop listening and return the full transcript, waiting briefly for the last words.
    public func stop() async -> String {
        rotation?.invalidate()
        rotation = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        let ending = lock.withLock { () -> SFSpeechAudioBufferRecognitionRequest? in
            defer { request = nil }
            return request
        }
        ending?.endAudio()
        for _ in 0 ..< 40 where lock.withLock({ !partial.isEmpty || !finishing.isEmpty }) {
            try? await Task.sleep(for: .milliseconds(50))
            if lock.withLock({ partial.isEmpty && finishing.isEmpty }) { break }
        }
        let transcript = lock.withLock { () -> String in
            if !partial.isEmpty { segments.append(partial); partial = "" }
            return segments.joined(separator: " ")
        }
        meetingLogger.info("meeting_transcription_stopped characters=\(transcript.count, privacy: .public)")
        return String(transcript.prefix(Self.maximumCharacters))
    }

    private func beginSegment() {
        let next = SFSpeechAudioBufferRecognitionRequest()
        next.requiresOnDeviceRecognition = true
        next.shouldReportPartialResults = true
        next.addsPunctuation = true
        next.taskHint = .dictation
        let previous = lock.withLock { () -> SFSpeechAudioBufferRecognitionRequest? in
            defer { request = next }
            return request
        }
        // Closing the previous request finalizes its words into a segment.
        previous?.endAudio()
        var started: SFSpeechRecognitionTask?
        started = recognizer.recognitionTask(with: next) { [weak self] result, error in
            guard let self else { return }
            self.lock.withLock {
                if let result {
                    let words = result.bestTranscription.formattedString
                    if result.isFinal {
                        if !words.isEmpty { self.segments.append(words) }
                        if self.request !== next { self.partial = "" }
                        self.finishing.removeAll { $0 === started }
                    } else if self.request === next {
                        self.partial = words
                    }
                } else if error != nil {
                    self.finishing.removeAll { $0 === started }
                }
            }
        }
        lock.withLock {
            if let started { finishing.append(started) }
            task = started
        }
    }
}

public extension ChatAttachment {
    /// Split a meeting transcript into the text documents one request can carry.
    static func meetingTranscript(_ transcript: String, title: String) -> [ChatAttachment] {
        var parts: [String] = []
        var current = ""
        for word in transcript.split(separator: " ", omittingEmptySubsequences: true) {
            if current.utf8.count + word.utf8.count + 1 > 30_000 {
                parts.append(current)
                current = ""
            }
            current += (current.isEmpty ? "" : " ") + word
        }
        if !current.isEmpty { parts.append(current) }
        return parts.prefix(4).enumerated().map { index, text in
            let name = parts.count == 1 ? "\(title).txt" : "\(title) (parte \(index + 1)).txt"
            return ChatAttachment(document: ChatDocumentAttachment(name: name, content: text))
        }
    }
}
