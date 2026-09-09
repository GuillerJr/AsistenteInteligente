@preconcurrency import AVFoundation
import Foundation
import OSLog
@preconcurrency import Speech

private let speechLogger = Logger(subsystem: "ai.aegis.audio", category: "Speech")

enum SpeechEndpointTiming {
    static let trailingSilenceMilliseconds = 800

    static func releaseFrames(intervalMilliseconds: Int) -> Int {
        max(
            1,
            Int(ceil(
                Double(trailingSilenceMilliseconds) / Double(intervalMilliseconds)
            ))
        )
    }
}

public enum SpeechRecognitionPermission: String, Codable, Sendable {
    case authorized
    case denied
    case restricted
    case notDetermined = "not_determined"
    case unknown

    public static var current: SpeechRecognitionPermission {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            .authorized
        case .denied:
            .denied
        case .restricted:
            .restricted
        case .notDetermined:
            .notDetermined
        @unknown default:
            .unknown
        }
    }
}

public enum SpeechLocale {
    public static func normalized(_ rawValue: String) -> String? {
        let replaced = rawValue.replacingOccurrences(of: "_", with: "-")
        let parts = replaced.split(separator: "-", omittingEmptySubsequences: false)
        guard (1 ... 3).contains(parts.count), (2 ... 3).contains(parts[0].count) else {
            return nil
        }
        guard parts.allSatisfy({ part in
            (2 ... 8).contains(part.count) && part.unicodeScalars.allSatisfy(isASCIIAlphaNumeric)
        }) else {
            return nil
        }
        guard parts[0].unicodeScalars.allSatisfy(isASCIIAlpha) else {
            return nil
        }

        var normalizedParts = [parts[0].lowercased()]
        for part in parts.dropFirst() {
            if part.count == 4, part.unicodeScalars.allSatisfy(isASCIIAlpha) {
                normalizedParts.append(part.prefix(1).uppercased() + part.dropFirst().lowercased())
            } else {
                normalizedParts.append(part.uppercased())
            }
        }
        let normalized = normalizedParts.joined(separator: "-")
        return normalized.count <= 35 ? normalized : nil
    }

    private static func isASCIIAlpha(_ scalar: Unicode.Scalar) -> Bool {
        (65 ... 90).contains(scalar.value) || (97 ... 122).contains(scalar.value)
    }

    private static func isASCIIAlphaNumeric(_ scalar: Unicode.Scalar) -> Bool {
        isASCIIAlpha(scalar) || (48 ... 57).contains(scalar.value)
    }
}

public struct SpeechStatusEvent: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let type: String
    public let state: String
    public let microphonePermission: MicrophonePermission
    public let speechPermission: SpeechRecognitionPermission
    public let localeIdentifier: String
    public let localeSupported: Bool
    public let recognizerAvailable: Bool
    public let onDeviceAvailable: Bool
    public let transcriptAvailable: Bool?
    public let errorCode: String?

    public var ready: Bool {
        microphonePermission == .authorized
            && speechPermission == .authorized
            && localeSupported
            && recognizerAvailable
            && onDeviceAvailable
    }

    public static func inspect(
        state: String,
        localeIdentifier: String,
        transcriptAvailable: Bool? = nil,
        errorCode: String? = nil
    ) -> SpeechStatusEvent? {
        guard let localeIdentifier = SpeechLocale.normalized(localeIdentifier) else {
            return nil
        }
        let supported = SFSpeechRecognizer.supportedLocales().contains { locale in
            SpeechLocale.normalized(locale.identifier) == localeIdentifier
        }
        let recognizer = supported
            ? SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier))
            : nil
        return SpeechStatusEvent(
            state: state,
            microphonePermission: .current,
            speechPermission: .current,
            localeIdentifier: localeIdentifier,
            localeSupported: supported,
            recognizerAvailable: recognizer?.isAvailable ?? false,
            onDeviceAvailable: recognizer?.supportsOnDeviceRecognition ?? false,
            transcriptAvailable: transcriptAvailable,
            errorCode: errorCode
        )
    }

    public init(
        state: String,
        microphonePermission: MicrophonePermission,
        speechPermission: SpeechRecognitionPermission,
        localeIdentifier: String,
        localeSupported: Bool,
        recognizerAvailable: Bool,
        onDeviceAvailable: Bool,
        transcriptAvailable: Bool? = nil,
        errorCode: String? = nil
    ) {
        schemaVersion = "1.0"
        type = "speech.status"
        self.state = state
        self.microphonePermission = microphonePermission
        self.speechPermission = speechPermission
        self.localeIdentifier = localeIdentifier
        self.localeSupported = localeSupported
        self.recognizerAvailable = recognizerAvailable
        self.onDeviceAvailable = onDeviceAvailable
        self.transcriptAvailable = transcriptAvailable
        self.errorCode = errorCode
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case state
        case microphonePermission = "microphone_permission"
        case speechPermission = "speech_permission"
        case localeIdentifier = "locale_identifier"
        case localeSupported = "locale_supported"
        case recognizerAvailable = "recognizer_available"
        case onDeviceAvailable = "on_device_available"
        case transcriptAvailable = "transcript_available"
        case errorCode = "error_code"
    }
}

public struct SpeechTranscriptEvent: Codable, Equatable, Sendable {
    public static let maximumTextCharacters = 4_096
    public static let maximumJSONBytes = 16_384

    public let schemaVersion: String
    public let type: String
    public let captureID: UUID
    public let sequence: UInt64
    public let text: String
    public let localeIdentifier: String
    public let durationMilliseconds: UInt64
    public let isFinal: Bool
    public let onDevice: Bool
    public let confidence: Double?
    public let speakerID: String?
    public let speakerConfidence: Double?
    public let soleSpeakerProfile: Bool
    public let ownerSpeakerProfile: Bool
    public let ownerPresenceVerified: Bool

    public init?(
        captureID: UUID,
        sequence: UInt64,
        text: String,
        localeIdentifier: String,
        durationMilliseconds: UInt64,
        isFinal: Bool,
        confidence: Double?,
        speakerID: String? = nil,
        speakerConfidence: Double? = nil,
        soleSpeakerProfile: Bool = false,
        ownerSpeakerProfile: Bool = false,
        ownerPresenceVerified: Bool = false
    ) {
        guard let localeIdentifier = SpeechLocale.normalized(localeIdentifier) else {
            return nil
        }
        let normalizedText = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard !normalizedText.isEmpty else {
            return nil
        }
        schemaVersion = "1.0"
        type = "speech.transcript"
        self.captureID = captureID
        self.sequence = sequence
        self.text = String(normalizedText.prefix(Self.maximumTextCharacters))
        self.localeIdentifier = localeIdentifier
        self.durationMilliseconds = min(durationMilliseconds, 60_000)
        self.isFinal = isFinal
        onDevice = true
        if let confidence, confidence.isFinite {
            self.confidence = min(max(confidence, 0), 1)
        } else {
            self.confidence = nil
        }
        guard (speakerID == nil) == (speakerConfidence == nil) else {
            return nil
        }
        guard !(soleSpeakerProfile || ownerSpeakerProfile) || speakerID != nil else {
            return nil
        }
        guard !ownerPresenceVerified || ownerSpeakerProfile else { return nil }
        self.soleSpeakerProfile = soleSpeakerProfile
        self.ownerSpeakerProfile = ownerSpeakerProfile
        self.ownerPresenceVerified = ownerPresenceVerified
        if let speakerID, let speakerConfidence {
            guard
                SpeakerIdentityCapability.isValidSpeakerLabel(speakerID),
                speakerConfidence.isFinite,
                (0 ... 1).contains(speakerConfidence)
            else {
                return nil
            }
            self.speakerID = speakerID
            self.speakerConfidence = speakerConfidence
        } else {
            self.speakerID = nil
            self.speakerConfidence = nil
        }
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case captureID = "capture_id"
        case sequence
        case text
        case localeIdentifier = "locale_identifier"
        case durationMilliseconds = "duration_milliseconds"
        case isFinal = "is_final"
        case onDevice = "on_device"
        case confidence
        case speakerID = "speaker_id"
        case speakerConfidence = "speaker_confidence"
        case soleSpeakerProfile = "sole_speaker_profile"
        case ownerSpeakerProfile = "owner_speaker_profile"
        case ownerPresenceVerified = "owner_presence_verified"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let schemaVersion = try values.decode(String.self, forKey: .schemaVersion)
        let type = try values.decode(String.self, forKey: .type)
        let captureID = try values.decode(UUID.self, forKey: .captureID)
        let sequence = try values.decode(UInt64.self, forKey: .sequence)
        let text = try values.decode(String.self, forKey: .text)
        let localeIdentifier = try values.decode(String.self, forKey: .localeIdentifier)
        let durationMilliseconds = try values.decode(UInt64.self, forKey: .durationMilliseconds)
        let isFinal = try values.decode(Bool.self, forKey: .isFinal)
        let onDevice = try values.decode(Bool.self, forKey: .onDevice)
        let confidence = try values.decodeIfPresent(Double.self, forKey: .confidence)
        let speakerID = try values.decodeIfPresent(String.self, forKey: .speakerID)
        let speakerConfidence = try values.decodeIfPresent(Double.self, forKey: .speakerConfidence)
        let soleSpeakerProfile = try values.decodeIfPresent(
            Bool.self,
            forKey: .soleSpeakerProfile
        ) ?? false
        let ownerSpeakerProfile = try values.decodeIfPresent(
            Bool.self,
            forKey: .ownerSpeakerProfile
        ) ?? false
        let ownerPresenceVerified = try values.decodeIfPresent(
            Bool.self,
            forKey: .ownerPresenceVerified
        ) ?? false
        guard
            schemaVersion == "1.0",
            type == "speech.transcript",
            onDevice,
            sequence <= UInt64(Int64.max),
            durationMilliseconds <= 60_000,
            text.count <= Self.maximumTextCharacters,
            let event = Self(
                captureID: captureID,
                sequence: sequence,
                text: text,
                localeIdentifier: localeIdentifier,
                durationMilliseconds: durationMilliseconds,
                isFinal: isFinal,
                confidence: confidence,
                speakerID: speakerID,
                speakerConfidence: speakerConfidence,
                soleSpeakerProfile: soleSpeakerProfile,
                ownerSpeakerProfile: ownerSpeakerProfile,
                ownerPresenceVerified: ownerPresenceVerified
            ),
            event.text == text,
            event.localeIdentifier == localeIdentifier,
            event.confidence == confidence,
            event.speakerID == speakerID,
            event.speakerConfidence == speakerConfidence,
            event.soleSpeakerProfile == soleSpeakerProfile,
            event.ownerSpeakerProfile == ownerSpeakerProfile,
            event.ownerPresenceVerified == ownerPresenceVerified
        else {
            throw DecodingError.dataCorrupted(
                .init(
                    codingPath: decoder.codingPath,
                    debugDescription: "invalid local transcript event"
                )
            )
        }
        self = event
    }

    public static func decodeStrictJSON(_ data: Data) throws -> SpeechTranscriptEvent {
        guard !data.isEmpty, data.count <= maximumJSONBytes,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DecodingError.dataCorrupted(
                .init(codingPath: [], debugDescription: "invalid transcript JSON")
            )
        }
        let requiredKeys: Set<String> = [
            "schema_version", "type", "capture_id", "sequence", "text", "locale_identifier",
            "duration_milliseconds", "is_final", "on_device",
        ]
        let optionalKeys = Set([
            "confidence", "speaker_id", "speaker_confidence", "sole_speaker_profile",
            "owner_speaker_profile", "owner_presence_verified",
        ])
        let keys = Set(object.keys)
        let hasSpeakerID = keys.contains("speaker_id")
        let hasSpeakerConfidence = keys.contains("speaker_confidence")
        guard
            requiredKeys.isSubset(of: keys),
            keys.isSubset(of: requiredKeys.union(optionalKeys)),
            hasSpeakerID == hasSpeakerConfidence
        else {
            throw DecodingError.dataCorrupted(
                .init(codingPath: [], debugDescription: "unexpected transcript fields")
            )
        }
        return try JSONDecoder().decode(Self.self, from: data)
    }
}

public enum LocalSpeechTranscriberError: Error, Equatable {
    case invalidConfiguration
    case microphonePermissionRequired(MicrophonePermission)
    case speechPermissionRequired(SpeechRecognitionPermission)
    case unsupportedLocale
    case recognizerUnavailable
    case onDeviceRecognitionUnavailable
    case invalidInputFormat
    case voiceActivityUnavailable
    case speakerIdentityUnverified
    case noAudibleInput
    case recognitionFailed
    case cancelled
}

public enum VoiceCapturePolicy {
    /// A normal conversational turn must never hold the microphone indefinitely.
    /// Silero ends the turn after 800 ms of post-speech silence; this ceiling only
    /// handles continuous noise or a VAD stream that never reaches an endpoint.
    public static let normalTurnMaximumSeconds: TimeInterval = 20
    public static let initialSilenceMaximumSeconds: TimeInterval = 5

    public static func boundedNormalTurnDuration(_ seconds: TimeInterval) -> TimeInterval {
        guard seconds.isFinite else { return normalTurnMaximumSeconds }
        return min(max(seconds, 1), normalTurnMaximumSeconds)
    }
}

private final class SpeechResultEmitter: @unchecked Sendable {
    private let captureID: UUID
    private let localeIdentifier: String
    private let startedAtNanoseconds: UInt64
    private let writer: NDJSONWriter
    private let failureHandler: @Sendable () -> Void
    private let lock = NSLock()
    private let completion = DispatchSemaphore(value: 0)
    private var sequence: UInt64 = 0
    private var lastSignature: String?
    private var completionSignaled = false
    private var finalTranscriptProduced = false
    private var finalTranscript: SpeechTranscriptEvent?
    private var recognitionFailed = false

    init(
        captureID: UUID,
        localeIdentifier: String,
        startedAtNanoseconds: UInt64,
        writer: NDJSONWriter,
        failureHandler: @escaping @Sendable () -> Void = {}
    ) {
        self.captureID = captureID
        self.localeIdentifier = localeIdentifier
        self.startedAtNanoseconds = startedAtNanoseconds
        self.writer = writer
        self.failureHandler = failureHandler
    }

    var hasFinalTranscript: Bool {
        lock.withLock { finalTranscriptProduced }
    }

    var completedTranscript: SpeechTranscriptEvent? {
        lock.withLock { finalTranscript }
    }

    var hasRecognitionFailure: Bool {
        lock.withLock { recognitionFailed }
    }

    func receive(result: SFSpeechRecognitionResult?, error: Error?) {
        if let result {
            let text = result.bestTranscription.formattedString
            let confidence = Self.averageConfidence(result.bestTranscription.segments)
            let isFinal = result.isFinal
            let signature = "\(isFinal):\(text)"
            let currentSequence: UInt64? = lock.withLock {
                guard signature != lastSignature else {
                    return nil
                }
                lastSignature = signature
                defer { sequence &+= 1 }
                return sequence
            }
            if let currentSequence,
               let event = SpeechTranscriptEvent(
                   captureID: captureID,
                   sequence: currentSequence,
                   text: text,
                   localeIdentifier: localeIdentifier,
                   durationMilliseconds: min(
                       (DispatchTime.now().uptimeNanoseconds - startedAtNanoseconds) / 1_000_000,
                       60_000
                   ),
                   isFinal: isFinal,
                   confidence: confidence
               ) {
                try? writer.write(event)
                if isFinal {
                    lock.withLock {
                        finalTranscriptProduced = true
                        finalTranscript = event
                    }
                }
            }
            if isFinal {
                signalCompletion()
            }
        }
        if let error {
            let firstFailure = lock.withLock {
                guard !recognitionFailed else { return false }
                recognitionFailed = true
                return true
            }
            if firstFailure {
                let failure = error as NSError
                speechLogger.error(
                    "recognition_error domain=\(failure.domain, privacy: .public) code=\(failure.code)"
                )
                failureHandler()
            }
            signalCompletion()
        }
    }

    func waitForCompletion(timeoutSeconds: Double) -> Bool {
        completion.wait(timeout: .now() + timeoutSeconds) == .success
    }

    private func signalCompletion() {
        let shouldSignal = lock.withLock {
            guard !completionSignaled else {
                return false
            }
            completionSignaled = true
            return true
        }
        if shouldSignal {
            completion.signal()
        }
    }

    private static func averageConfidence(_ segments: [SFTranscriptionSegment]) -> Double? {
        guard !segments.isEmpty else {
            return nil
        }
        let total = segments.reduce(0.0) { $0 + Double($1.confidence) }
        return total / Double(segments.count)
    }
}

struct SpeechEndpointDetector: Sendable {
    enum State: Equatable, Sendable {
        case awaitingSpeech
        case speaking
        case ended
    }

    private(set) var state = State.awaitingSpeech
    private var utteranceID: UUID?

    mutating func consume(_ event: SpeechActivityEvent) {
        switch (state, event.event) {
        case (.awaitingSpeech, .started):
            utteranceID = event.utteranceID
            state = .speaking
        case (.speaking, .ended) where event.utteranceID == utteranceID:
            utteranceID = nil
            state = .ended
        default:
            break
        }
    }
}

private final class SpeechEndpointWaiter: @unchecked Sendable {
    private let lock = NSLock()
    private let signal = DispatchSemaphore(value: 0)
    private var detector = SpeechEndpointDetector()
    private var recognitionFailed = false
    private var cancelled = false

    var wasCancelled: Bool {
        lock.withLock { cancelled }
    }

    func receive(_ event: SpeechActivityEvent) {
        let changed = lock.withLock {
            let previousState = detector.state
            detector.consume(event)
            return detector.state != previousState
        }
        if changed {
            signal.signal()
        }
    }

    func failRecognition() {
        let shouldSignal = lock.withLock {
            guard !recognitionFailed else { return false }
            recognitionFailed = true
            return true
        }
        if shouldSignal {
            signal.signal()
        }
    }

    func cancel() {
        let shouldSignal = lock.withLock {
            guard !cancelled else { return false }
            cancelled = true
            return true
        }
        if shouldSignal { signal.signal() }
    }

    func wait(maximumDurationSeconds: Double, initialSilenceSeconds: Double) {
        let started = DispatchTime.now()
        let maximumDeadline = started + maximumDurationSeconds
        let initialDeadline = started + min(initialSilenceSeconds, maximumDurationSeconds)
        while true {
            let state = lock.withLock { detector.state }
            let completion = lock.withLock { (recognitionFailed, cancelled) }
            if state == .ended || completion.0 || completion.1 {
                return
            }
            let deadline = state == .awaitingSpeech ? initialDeadline : maximumDeadline
            if signal.wait(timeout: deadline) == .timedOut {
                return
            }
        }
    }
}

private final class SileroSpeechEventEmitter: @unchecked Sendable {
    private let lock = NSLock()
    private let handler: @Sendable (SpeechActivityEvent) -> Void
    private var utteranceID: UUID?
    private var startedAtNanoseconds: UInt64?
    private var sequence: UInt64 = 0

    init(handler: @escaping @Sendable (SpeechActivityEvent) -> Void) {
        self.handler = handler
    }

    func receive(_ event: SileroVADEvent) {
        let activity = lock.withLock { () -> SpeechActivityEvent? in
            let now = DispatchTime.now().uptimeNanoseconds
            defer { sequence &+= 1 }
            switch event {
            case .speechStarted:
                guard utteranceID == nil else { return nil }
                let identifier = UUID()
                utteranceID = identifier
                startedAtNanoseconds = now
                return SpeechActivityEvent(
                    event: .started,
                    utteranceID: identifier,
                    sampleSequence: sequence,
                    monotonicNanoseconds: now
                )
            case .speechEnded:
                guard let identifier = utteranceID, let startedAtNanoseconds else {
                    return nil
                }
                utteranceID = nil
                self.startedAtNanoseconds = nil
                return SpeechActivityEvent(
                    event: .ended,
                    utteranceID: identifier,
                    sampleSequence: sequence,
                    monotonicNanoseconds: now,
                    durationMilliseconds: (now - startedAtNanoseconds) / 1_000_000
                )
            }
        }
        if let activity { handler(activity) }
    }
}

private final class SpeechProcessorFailureFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var failed = false

    var value: Bool { lock.withLock { failed } }

    func set() {
        lock.withLock { failed = true }
    }
}

public final class LocalSpeechTranscriber: @unchecked Sendable {
    private let writer: NDJSONWriter
    private let analyzer: AudioMeterAnalyzer
    private let activityHandler: (@Sendable (Float) -> Void)?
    private let speakerModelURL: URL?
    private let selectedOwnerIdentifier: String?
    private let expectedSpeakerModelFingerprint: String?
    private let cancellationLock = NSLock()
    private var activeEndpointWaiter: SpeechEndpointWaiter?
    private var cancellationRequested = false
    private var ownerVoiceVariant: CapturedOwnerVoiceVariant?

    public var capturedOwnerVoiceVariant: CapturedOwnerVoiceVariant? {
        cancellationLock.withLock { ownerVoiceVariant }
    }

    public init(
        writer: NDJSONWriter = NDJSONWriter(),
        analyzer: AudioMeterAnalyzer = AudioMeterAnalyzer(),
        activityHandler: (@Sendable (Float) -> Void)? = nil,
        speakerModelURL: URL? = SpeakerIdentityCapability.modelURL(),
        selectedOwnerIdentifier: String? = nil,
        expectedSpeakerModelFingerprint: String? = nil
    ) {
        self.writer = writer
        self.analyzer = analyzer
        self.activityHandler = activityHandler
        self.speakerModelURL = speakerModelURL
        self.selectedOwnerIdentifier = selectedOwnerIdentifier
        self.expectedSpeakerModelFingerprint = expectedSpeakerModelFingerprint
    }

    public func cancel() {
        let waiter = cancellationLock.withLock {
            cancellationRequested = true
            return activeEndpointWaiter
        }
        waiter?.cancel()
    }

    @discardableResult
    public func run(
        durationSeconds: TimeInterval,
        intervalMilliseconds: Int,
        localeIdentifier rawLocaleIdentifier: String
    ) throws -> Bool {
        try runForFinalTranscript(
            durationSeconds: durationSeconds,
            intervalMilliseconds: intervalMilliseconds,
            localeIdentifier: rawLocaleIdentifier
        ) != nil
    }

    public func runForFinalTranscript(
        durationSeconds: TimeInterval,
        intervalMilliseconds: Int,
        localeIdentifier rawLocaleIdentifier: String
    ) throws -> SpeechTranscriptEvent? {
        guard
            durationSeconds.isFinite,
            (1 ... 60).contains(durationSeconds),
            (20 ... 250).contains(intervalMilliseconds)
        else {
            throw LocalSpeechTranscriberError.invalidConfiguration
        }
        let microphonePermission = MicrophonePermission.current
        guard microphonePermission == .authorized else {
            throw LocalSpeechTranscriberError.microphonePermissionRequired(microphonePermission)
        }
        let speechPermission = SpeechRecognitionPermission.current
        guard speechPermission == .authorized else {
            throw LocalSpeechTranscriberError.speechPermissionRequired(speechPermission)
        }
        guard let localeIdentifier = SpeechLocale.normalized(rawLocaleIdentifier) else {
            throw LocalSpeechTranscriberError.unsupportedLocale
        }
        let locale = Locale(identifier: localeIdentifier)
        let localeSupported = SFSpeechRecognizer.supportedLocales().contains {
            SpeechLocale.normalized($0.identifier) == localeIdentifier
        }
        guard localeSupported, let recognizer = SFSpeechRecognizer(locale: locale) else {
            throw LocalSpeechTranscriberError.unsupportedLocale
        }
        guard recognizer.supportsOnDeviceRecognition else {
            throw LocalSpeechTranscriberError.onDeviceRecognitionUnavailable
        }
        guard recognizer.isAvailable else {
            throw LocalSpeechTranscriberError.recognizerUnavailable
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        request.addsPunctuation = true
        request.taskHint = .dictation

        let processor: SpeechInputProcessor
        do {
            processor = try SpeechInputProcessor()
        } catch {
            throw LocalSpeechTranscriberError.voiceActivityUnavailable
        }
        let format = processor.processingFormat
        guard format.sampleRate >= 16_000, format.channelCount > 0 else {
            throw LocalSpeechTranscriberError.invalidInputFormat
        }

        let captureID = UUID()
        let voiceAccumulator = selectedOwnerIdentifier.map { _ in
            OwnerVoiceSampleAccumulator()
        }
        let startedAtNanoseconds = DispatchTime.now().uptimeNanoseconds
        let endpointWaiter = SpeechEndpointWaiter()
        let registered = cancellationLock.withLock {
            guard !cancellationRequested, activeEndpointWaiter == nil else { return false }
            activeEndpointWaiter = endpointWaiter
            return true
        }
        guard registered else { throw LocalSpeechTranscriberError.cancelled }
        defer {
            cancellationLock.withLock {
                if activeEndpointWaiter === endpointWaiter {
                    activeEndpointWaiter = nil
                }
            }
        }
        let emitter = SpeechResultEmitter(
            captureID: captureID,
            localeIdentifier: localeIdentifier,
            startedAtNanoseconds: startedAtNanoseconds,
            writer: writer,
            failureHandler: { endpointWaiter.failRecognition() }
        )
        let task = recognizer.recognitionTask(with: request) { result, error in
            emitter.receive(result: result, error: error)
        }
        let meterProcessor = MeterProcessor(
            analyzer: analyzer,
            writer: writer,
            activityHandler: activityHandler,
            detectsSpeechActivity: false
        )
        let sileroEmitter = SileroSpeechEventEmitter { endpointWaiter.receive($0) }
        let speakerSession = speakerModelURL.flatMap {
            try? SpeakerIdentitySession(
                format: format,
                modelURL: $0,
                selectedOwnerIdentifier: selectedOwnerIdentifier,
                expectedModelFingerprint: expectedSpeakerModelFingerprint
            )
        }
        let processorFailure = SpeechProcessorFailureFlag()
        do {
            try processor.start(
                bufferMilliseconds: 32,
                handler: { buffer, frame in
                    request.append(buffer)
                    meterProcessor.process(buffer: buffer, sampleRateHz: format.sampleRate)
                    speakerSession?.analyze(buffer)
                    voiceAccumulator?.append(buffer)
                    if let event = frame.vadEvent {
                        sileroEmitter.receive(event)
                    }
                },
                failureHandler: { _ in
                    processorFailure.set()
                    endpointWaiter.failRecognition()
                }
            )
        } catch {
            task.cancel()
            throw LocalSpeechTranscriberError.voiceActivityUnavailable
        }
        defer {
            processor.stop()
            task.cancel()
        }
        if let status = SpeechStatusEvent.inspect(
            state: "running",
            localeIdentifier: localeIdentifier
        ) {
            try writer.write(status)
        }
        endpointWaiter.wait(
            maximumDurationSeconds: durationSeconds,
            initialSilenceSeconds: min(
                VoiceCapturePolicy.initialSilenceMaximumSeconds,
                durationSeconds
            )
        )
        if endpointWaiter.wasCancelled {
            throw LocalSpeechTranscriberError.cancelled
        }
        processor.stop()
        request.endAudio()
        task.finish()
        meterProcessor.flush()
        if processorFailure.value {
            throw LocalSpeechTranscriberError.voiceActivityUnavailable
        }
        let speakerIdentity = speakerSession?.finish(timeoutSeconds: 0.6)
        if
            let speakerIdentity,
            let selectedOwnerIdentifier,
            speakerIdentity.identifier == selectedOwnerIdentifier,
            (0.65 ... 0.77).contains(speakerIdentity.confidence),
            let sample = voiceAccumulator?.finish(
                captureID: captureID,
                ownerIdentifier: selectedOwnerIdentifier
            )
        {
            cancellationLock.withLock { ownerVoiceVariant = sample }
        }
        _ = emitter.waitForCompletion(timeoutSeconds: 3)

        if emitter.hasRecognitionFailure {
            throw LocalSpeechTranscriberError.recognitionFailed
        }
        if !emitter.hasFinalTranscript, !meterProcessor.hasAudibleInput {
            throw LocalSpeechTranscriberError.noAudibleInput
        }

        let transcriptAvailable = emitter.hasFinalTranscript
        if let status = SpeechStatusEvent.inspect(
            state: "completed",
            localeIdentifier: localeIdentifier,
            transcriptAvailable: transcriptAvailable
        ) {
            try writer.write(status)
        }
        guard let transcript = emitter.completedTranscript else { return nil }
        if let selectedOwnerIdentifier {
            guard
                SpeakerTurnAdmissionPolicy.accepts(
                    speakerIdentity,
                    selectedOwnerIdentifier: selectedOwnerIdentifier,
                    resolvedOwnerIdentifier: speakerSession?.ownerSpeakerIdentifier
                )
            else {
                throw LocalSpeechTranscriberError.speakerIdentityUnverified
            }
        }
        guard let speakerIdentity else { return transcript }
        return SpeechTranscriptEvent(
            captureID: transcript.captureID,
            sequence: transcript.sequence,
            text: transcript.text,
            localeIdentifier: transcript.localeIdentifier,
            durationMilliseconds: transcript.durationMilliseconds,
            isFinal: transcript.isFinal,
            confidence: transcript.confidence,
            speakerID: speakerIdentity.identifier,
            speakerConfidence: speakerIdentity.confidence,
            soleSpeakerProfile: speakerSession?.soleSpeakerIdentifier == speakerIdentity.identifier,
            ownerSpeakerProfile: speakerSession?.ownerSpeakerIdentifier == speakerIdentity.identifier
        )
    }
}
