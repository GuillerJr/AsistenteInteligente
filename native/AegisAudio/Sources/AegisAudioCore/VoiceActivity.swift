import Foundation

public enum SpeechEventType: String, Codable, Sendable {
    case started
    case ended
}

public struct SpeechActivityEvent: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let event: SpeechEventType
    public let utteranceID: UUID
    public let sampleSequence: UInt64
    public let monotonicNanoseconds: UInt64
    public let durationMilliseconds: UInt64?

    public init(
        schemaVersion: String = "1.0",
        event: SpeechEventType,
        utteranceID: UUID,
        sampleSequence: UInt64,
        monotonicNanoseconds: UInt64,
        durationMilliseconds: UInt64? = nil
    ) {
        precondition((event == .started) == (durationMilliseconds == nil))
        self.schemaVersion = schemaVersion
        self.event = event
        self.utteranceID = utteranceID
        self.sampleSequence = sampleSequence
        self.monotonicNanoseconds = monotonicNanoseconds
        self.durationMilliseconds = durationMilliseconds
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case event
        case utteranceID = "utterance_id"
        case sampleSequence = "sample_sequence"
        case monotonicNanoseconds = "monotonic_nanoseconds"
        case durationMilliseconds = "duration_milliseconds"
    }
}

public struct VoiceActivityConfiguration: Equatable, Sendable {
    public let startThresholdRMS: Float
    public let stopThresholdRMS: Float
    public let attackFrames: Int
    public let releaseFrames: Int

    public init(
        startThresholdRMS: Float = 0.02,
        stopThresholdRMS: Float = 0.008,
        attackFrames: Int = 2,
        releaseFrames: Int = 8
    ) {
        precondition(startThresholdRMS > stopThresholdRMS)
        precondition(stopThresholdRMS >= 0)
        precondition(startThresholdRMS <= 1)
        precondition(attackFrames >= 1)
        precondition(releaseFrames >= 1)
        self.startThresholdRMS = startThresholdRMS
        self.stopThresholdRMS = stopThresholdRMS
        self.attackFrames = attackFrames
        self.releaseFrames = releaseFrames
    }
}

public struct VoiceActivityDetector: Sendable {
    public private(set) var isSpeaking = false

    private let configuration: VoiceActivityConfiguration
    private let utteranceIDFactory: @Sendable () -> UUID
    private var attackCount = 0
    private var releaseCount = 0
    private var activeUtteranceID: UUID?
    private var startedAtNanoseconds: UInt64?
    private var lastSequence: UInt64?
    private var lastMonotonicNanoseconds: UInt64?

    public init(
        configuration: VoiceActivityConfiguration = VoiceActivityConfiguration(),
        utteranceIDFactory: @escaping @Sendable () -> UUID = { UUID() }
    ) {
        self.configuration = configuration
        self.utteranceIDFactory = utteranceIDFactory
    }

    public mutating func consume(_ sample: AudioMeterSample) -> SpeechActivityEvent? {
        guard isNewer(sample) else {
            return nil
        }
        lastSequence = sample.sequence
        lastMonotonicNanoseconds = sample.monotonicNanoseconds

        if !isSpeaking {
            releaseCount = 0
            guard sample.rms >= configuration.startThresholdRMS else {
                attackCount = 0
                return nil
            }
            attackCount += 1
            guard attackCount >= configuration.attackFrames else {
                return nil
            }

            attackCount = 0
            isSpeaking = true
            let utteranceID = utteranceIDFactory()
            activeUtteranceID = utteranceID
            startedAtNanoseconds = sample.monotonicNanoseconds
            return SpeechActivityEvent(
                event: .started,
                utteranceID: utteranceID,
                sampleSequence: sample.sequence,
                monotonicNanoseconds: sample.monotonicNanoseconds
            )
        }

        attackCount = 0
        guard sample.rms < configuration.stopThresholdRMS else {
            releaseCount = 0
            return nil
        }
        releaseCount += 1
        guard
            releaseCount >= configuration.releaseFrames,
            let utteranceID = activeUtteranceID,
            let startedAtNanoseconds
        else {
            return nil
        }

        releaseCount = 0
        isSpeaking = false
        activeUtteranceID = nil
        self.startedAtNanoseconds = nil
        let elapsed = sample.monotonicNanoseconds - startedAtNanoseconds
        return SpeechActivityEvent(
            event: .ended,
            utteranceID: utteranceID,
            sampleSequence: sample.sequence,
            monotonicNanoseconds: sample.monotonicNanoseconds,
            durationMilliseconds: elapsed / 1_000_000
        )
    }

    private func isNewer(_ sample: AudioMeterSample) -> Bool {
        guard let lastSequence, let lastMonotonicNanoseconds else {
            return true
        }
        return sample.sequence > lastSequence
            && sample.monotonicNanoseconds > lastMonotonicNanoseconds
    }
}
