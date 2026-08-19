import AVFoundation
import Foundation
import Testing
@testable import AegisAudioCore

private final class ActivityProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var storedValue: Float?

    func record(_ value: Float) {
        lock.withLock { storedValue = value }
    }

    var value: Float? {
        lock.withLock { storedValue }
    }
}

@Test func silenceProducesBoundedFloor() throws {
    let values: [Float] = [0, 0, 0, 0]
    let sample = values.withUnsafeBufferPointer {
        AudioMeterAnalyzer().analyze(
            samples: $0,
            sequence: 7,
            monotonicNanoseconds: 42,
            sampleRateHz: 48_000
        )
    }

    let result = try #require(sample)
    #expect(result.rms == 0)
    #expect(result.peak == 0)
    #expect(result.dbfs == -120)
    #expect(result.activity == 0)
    #expect(result.voiceActive == false)
    #expect(result.clipped == false)
}

@Test func analyzerUsesAccelerateForRMSAndPeak() throws {
    let values: [Float] = [-1, 0, 1, 0]
    let sample = values.withUnsafeBufferPointer {
        AudioMeterAnalyzer().analyze(
            samples: $0,
            sequence: 1,
            monotonicNanoseconds: 2,
            sampleRateHz: 44_100
        )
    }

    let result = try #require(sample)
    #expect(abs(result.rms - 0.707_106_77) < 0.000_01)
    #expect(result.peak == 1)
    #expect(result.voiceActive)
    #expect(result.clipped)
    #expect(result.frameCount == 4)
}

@Test func envelopeMatchesPythonIPCFieldNames() throws {
    let sample = AudioMeterSample(
        sequence: 3,
        monotonicNanoseconds: 4,
        sampleRateHz: 48_000,
        frameCount: 2_400,
        rms: 0.1,
        peak: 0.2,
        dbfs: -20,
        activity: 0.5,
        voiceActive: true,
        clipped: false
    )
    let data = try JSONEncoder().encode(AudioMeterEnvelope(sample: sample))
    let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    let encodedSample = try #require(object["sample"] as? [String: Any])

    #expect(object["type"] as? String == "audio.meter")
    #expect(encodedSample["schema_version"] as? String == "1.0")
    #expect(encodedSample["monotonic_nanoseconds"] != nil)
    #expect(encodedSample["sample_rate_hz"] != nil)
    #expect(encodedSample["voice_active"] as? Bool == true)
    #expect(encodedSample["pcm"] == nil)
    #expect(object["speech_event"] == nil)
}

@Test func meterProcessorForwardsOnlyNormalizedActivity() throws {
    let format = try #require(
        AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)
    )
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4))
    buffer.frameLength = 4
    let channel = try #require(buffer.floatChannelData?.pointee)
    channel[0] = 0.01
    channel[1] = -0.01
    channel[2] = 0.01
    channel[3] = -0.01

    let probe = ActivityProbe()
    let processor = MeterProcessor(
        analyzer: AudioMeterAnalyzer(),
        writer: NDJSONWriter(handle: .nullDevice),
        activityHandler: probe.record
    )
    processor.process(buffer: buffer, sampleRateHz: format.sampleRate)
    processor.flush()

    let activity = try #require(probe.value)
    #expect(activity > 0)
    #expect(activity < 1)
}
