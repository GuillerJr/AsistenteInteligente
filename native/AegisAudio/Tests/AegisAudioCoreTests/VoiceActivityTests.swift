import Foundation
import Testing
@testable import AegisAudioCore

private let fixedUtteranceID = UUID(uuidString: "01234567-89AB-CDEF-0123-456789ABCDEF")!

private func meterSample(sequence: UInt64, nanoseconds: UInt64, rms: Float) -> AudioMeterSample {
    AudioMeterSample(
        sequence: sequence,
        monotonicNanoseconds: nanoseconds,
        sampleRateHz: 48_000,
        frameCount: 2_400,
        rms: rms,
        peak: rms,
        dbfs: rms > 0 ? 20 * log10f(rms) : -120,
        activity: 0,
        voiceActive: rms >= 0.015,
        clipped: false
    )
}

@Test func isolatedPeakDoesNotOpenSpeechTurn() {
    var detector = VoiceActivityDetector(
        configuration: VoiceActivityConfiguration(attackFrames: 2, releaseFrames: 3),
        utteranceIDFactory: { fixedUtteranceID }
    )

    #expect(detector.consume(meterSample(sequence: 1, nanoseconds: 1_000, rms: 0.03)) == nil)
    #expect(detector.consume(meterSample(sequence: 2, nanoseconds: 2_000, rms: 0.001)) == nil)
    #expect(detector.isSpeaking == false)
}

@Test func sustainedVoiceStartsAndSilenceEndsSameUtterance() throws {
    var detector = VoiceActivityDetector(
        configuration: VoiceActivityConfiguration(attackFrames: 2, releaseFrames: 3),
        utteranceIDFactory: { fixedUtteranceID }
    )

    #expect(detector.consume(meterSample(sequence: 1, nanoseconds: 1_000_000_000, rms: 0.03)) == nil)
    let startCandidate = detector.consume(
        meterSample(sequence: 2, nanoseconds: 2_000_000_000, rms: 0.03)
    )
    let started = try #require(startCandidate)
    #expect(started.event == .started)
    #expect(started.utteranceID == fixedUtteranceID)
    #expect(started.durationMilliseconds == nil)
    #expect(detector.isSpeaking)

    #expect(detector.consume(meterSample(sequence: 3, nanoseconds: 3_000_000_000, rms: 0.001)) == nil)
    #expect(detector.consume(meterSample(sequence: 4, nanoseconds: 4_000_000_000, rms: 0.001)) == nil)
    let endCandidate = detector.consume(
        meterSample(sequence: 5, nanoseconds: 5_000_000_000, rms: 0.001)
    )
    let ended = try #require(endCandidate)
    #expect(ended.event == .ended)
    #expect(ended.utteranceID == started.utteranceID)
    #expect(ended.durationMilliseconds == 3_000)
    #expect(detector.isSpeaking == false)
}

@Test func hysteresisIgnoresBriefDropWhileSpeaking() throws {
    var detector = VoiceActivityDetector(
        configuration: VoiceActivityConfiguration(attackFrames: 1, releaseFrames: 2),
        utteranceIDFactory: { fixedUtteranceID }
    )
    let startCandidate = detector.consume(
        meterSample(sequence: 1, nanoseconds: 1_000, rms: 0.03)
    )
    _ = try #require(startCandidate)

    #expect(detector.consume(meterSample(sequence: 2, nanoseconds: 2_000, rms: 0.001)) == nil)
    #expect(detector.consume(meterSample(sequence: 3, nanoseconds: 3_000, rms: 0.01)) == nil)
    #expect(detector.isSpeaking)
}

@Test func replayedOrNonMonotonicSamplesCannotChangeState() {
    var detector = VoiceActivityDetector(
        configuration: VoiceActivityConfiguration(attackFrames: 2, releaseFrames: 2),
        utteranceIDFactory: { fixedUtteranceID }
    )

    #expect(detector.consume(meterSample(sequence: 2, nanoseconds: 2_000, rms: 0.03)) == nil)
    #expect(detector.consume(meterSample(sequence: 2, nanoseconds: 3_000, rms: 0.03)) == nil)
    #expect(detector.consume(meterSample(sequence: 3, nanoseconds: 1_000, rms: 0.03)) == nil)
    #expect(detector.isSpeaking == false)
}

@Test func speechEventEnvelopeMatchesPythonIPCFieldNames() throws {
    let sample = meterSample(sequence: 4, nanoseconds: 5_000, rms: 0.03)
    let event = SpeechActivityEvent(
        event: .started,
        utteranceID: fixedUtteranceID,
        sampleSequence: sample.sequence,
        monotonicNanoseconds: sample.monotonicNanoseconds
    )
    let data = try JSONEncoder().encode(
        AudioMeterEnvelope(sample: sample, speechEvent: event)
    )
    let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    let encodedEvent = try #require(object["speech_event"] as? [String: Any])

    #expect(encodedEvent["schema_version"] as? String == "1.0")
    #expect(encodedEvent["event"] as? String == "started")
    #expect(encodedEvent["sample_sequence"] as? Int == 4)
    #expect(encodedEvent["monotonic_nanoseconds"] as? Int == 5_000)
    #expect(encodedEvent["duration_milliseconds"] == nil)
}

@Test func endpointRequiresMatchingSpeechStartAndEnd() {
    let otherUtteranceID = UUID(uuidString: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")!
    var endpoint = SpeechEndpointDetector()

    endpoint.consume(
        SpeechActivityEvent(
            event: .ended,
            utteranceID: fixedUtteranceID,
            sampleSequence: 1,
            monotonicNanoseconds: 1_000,
            durationMilliseconds: 1
        )
    )
    #expect(endpoint.state == .awaitingSpeech)

    endpoint.consume(
        SpeechActivityEvent(
            event: .started,
            utteranceID: fixedUtteranceID,
            sampleSequence: 2,
            monotonicNanoseconds: 2_000
        )
    )
    #expect(endpoint.state == .speaking)

    endpoint.consume(
        SpeechActivityEvent(
            event: .ended,
            utteranceID: otherUtteranceID,
            sampleSequence: 3,
            monotonicNanoseconds: 3_000,
            durationMilliseconds: 1
        )
    )
    #expect(endpoint.state == .speaking)

    endpoint.consume(
        SpeechActivityEvent(
            event: .ended,
            utteranceID: fixedUtteranceID,
            sampleSequence: 4,
            monotonicNanoseconds: 4_000,
            durationMilliseconds: 2
        )
    )
    #expect(endpoint.state == .ended)
}
