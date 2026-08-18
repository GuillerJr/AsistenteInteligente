import Accelerate
import Foundation

public struct AudioMeterSample: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let sequence: UInt64
    public let monotonicNanoseconds: UInt64
    public let sampleRateHz: Double
    public let frameCount: Int
    public let rms: Float
    public let peak: Float
    public let dbfs: Float
    public let activity: Float
    public let voiceActive: Bool
    public let clipped: Bool

    public init(
        schemaVersion: String = "1.0",
        sequence: UInt64,
        monotonicNanoseconds: UInt64,
        sampleRateHz: Double,
        frameCount: Int,
        rms: Float,
        peak: Float,
        dbfs: Float,
        activity: Float,
        voiceActive: Bool,
        clipped: Bool
    ) {
        self.schemaVersion = schemaVersion
        self.sequence = sequence
        self.monotonicNanoseconds = monotonicNanoseconds
        self.sampleRateHz = sampleRateHz
        self.frameCount = frameCount
        self.rms = rms
        self.peak = peak
        self.dbfs = dbfs
        self.activity = activity
        self.voiceActive = voiceActive
        self.clipped = clipped
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case sequence
        case monotonicNanoseconds = "monotonic_nanoseconds"
        case sampleRateHz = "sample_rate_hz"
        case frameCount = "frame_count"
        case rms
        case peak
        case dbfs
        case activity
        case voiceActive = "voice_active"
        case clipped
    }
}

public struct AudioMeterEnvelope: Codable, Equatable, Sendable {
    public let type: String
    public let sample: AudioMeterSample

    public init(sample: AudioMeterSample) {
        type = "audio.meter"
        self.sample = sample
    }
}

public struct AudioMeterAnalyzer: Sendable {
    public let voiceThresholdRMS: Float
    public let clippingThreshold: Float
    public let activityFloorDBFS: Float
    public let activityCeilingDBFS: Float

    public init(
        voiceThresholdRMS: Float = 0.015,
        clippingThreshold: Float = 0.999,
        activityFloorDBFS: Float = -60,
        activityCeilingDBFS: Float = -12
    ) {
        precondition(voiceThresholdRMS > 0 && voiceThresholdRMS <= 1)
        precondition(clippingThreshold > 0 && clippingThreshold <= 1)
        precondition(activityFloorDBFS < activityCeilingDBFS)
        self.voiceThresholdRMS = voiceThresholdRMS
        self.clippingThreshold = clippingThreshold
        self.activityFloorDBFS = activityFloorDBFS
        self.activityCeilingDBFS = activityCeilingDBFS
    }

    public func analyze(
        samples: UnsafeBufferPointer<Float>,
        sequence: UInt64,
        monotonicNanoseconds: UInt64,
        sampleRateHz: Double
    ) -> AudioMeterSample? {
        guard !samples.isEmpty, let baseAddress = samples.baseAddress else {
            return nil
        }

        var measuredRMS: Float = 0
        var measuredPeak: Float = 0
        let count = vDSP_Length(samples.count)
        vDSP_rmsqv(baseAddress, 1, &measuredRMS, count)
        vDSP_maxmgv(baseAddress, 1, &measuredPeak, count)

        let boundedRMS = min(max(measuredRMS, 0), 1)
        let boundedPeak = min(max(measuredPeak, 0), 1)
        let dbfs = boundedRMS > 0 ? max(-120, 20 * log10f(boundedRMS)) : -120
        let activityRange = activityCeilingDBFS - activityFloorDBFS
        let activity = min(max((dbfs - activityFloorDBFS) / activityRange, 0), 1)

        return AudioMeterSample(
            sequence: sequence,
            monotonicNanoseconds: monotonicNanoseconds,
            sampleRateHz: sampleRateHz,
            frameCount: samples.count,
            rms: boundedRMS,
            peak: boundedPeak,
            dbfs: dbfs,
            activity: activity,
            voiceActive: boundedRMS >= voiceThresholdRMS,
            clipped: boundedPeak >= clippingThreshold
        )
    }
}
