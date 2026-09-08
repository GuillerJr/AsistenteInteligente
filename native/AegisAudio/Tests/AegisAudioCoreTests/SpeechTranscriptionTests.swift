@preconcurrency import AVFoundation
import Foundation
import Testing
@testable import AegisAudioCore

@Test func conversationalCaptureDurationIsStrictlyBounded() {
    #expect(VoiceCapturePolicy.boundedNormalTurnDuration(.infinity) == 20)
    #expect(VoiceCapturePolicy.boundedNormalTurnDuration(0) == 1)
    #expect(VoiceCapturePolicy.boundedNormalTurnDuration(8) == 8)
    #expect(VoiceCapturePolicy.boundedNormalTurnDuration(60) == 20)
    #expect(VoiceCapturePolicy.initialSilenceMaximumSeconds == 5)
}

@Test func speechInputCanonicalizesHardwarePCMWithoutOpeningTheMicrophone() throws {
    let hardwareFormat = try #require(
        AVAudioFormat(
            standardFormatWithSampleRate: 48_000,
            channels: 2
        )
    )
    let hardwareBuffer = try #require(
        AVAudioPCMBuffer(pcmFormat: hardwareFormat, frameCapacity: 960)
    )
    hardwareBuffer.frameLength = 960
    let channels = try #require(hardwareBuffer.floatChannelData)
    for index in 0 ..< 960 {
        channels[0][index] = 0.4
        channels[1][index] = 0.2
    }

    let canonical = try #require(
        SpeechInputProcessor.makeCanonicalSpeechBuffer(from: hardwareBuffer)
    )
    let samples = try #require(canonical.floatChannelData?.pointee)

    #expect(canonical.format.sampleRate == 16_000)
    #expect(canonical.format.channelCount == 1)
    #expect(canonical.frameLength == 320)
    #expect(abs(samples[160] - 0.3) < 0.000_1)
}

@Test func speechEndpointTimingKeepsATightBoundAcrossSupportedIntervals() {
    #expect(SpeechEndpointTiming.releaseFrames(intervalMilliseconds: 20) == 40)
    #expect(SpeechEndpointTiming.releaseFrames(intervalMilliseconds: 50) == 16)
    #expect(SpeechEndpointTiming.releaseFrames(intervalMilliseconds: 250) == 4)

    for interval in 20 ... 250 {
        let elapsed = SpeechEndpointTiming.releaseFrames(
            intervalMilliseconds: interval
        ) * interval
        #expect(elapsed >= SpeechEndpointTiming.trailingSilenceMilliseconds)
        #expect(elapsed < SpeechEndpointTiming.trailingSilenceMilliseconds + interval)
    }
}

@Test func speechLocaleNormalizationRejectsPathLikeInput() {
    #expect(SpeechLocale.normalized("es_US") == "es-US")
    #expect(SpeechLocale.normalized("zh-hans-cn") == "zh-Hans-CN")
    #expect(SpeechLocale.normalized("../../invalid") == nil)
    #expect(SpeechLocale.normalized("e") == nil)
}

@Test func transcriptNormalizesAndBoundsSensitiveText() throws {
    let captureID = UUID(uuidString: "01234567-89AB-CDEF-0123-456789ABCDEF")!
    let event = try #require(
        SpeechTranscriptEvent(
            captureID: captureID,
            sequence: 2,
            text: "  analiza\n\tel sistema  " + String(repeating: "x", count: 5_000),
            localeIdentifier: "es_US",
            durationMilliseconds: 80_000,
            isFinal: true,
            confidence: 1.5
        )
    )

    #expect(event.text.hasPrefix("analiza el sistema "))
    #expect(event.text.count == SpeechTranscriptEvent.maximumTextCharacters)
    #expect(event.localeIdentifier == "es-US")
    #expect(event.durationMilliseconds == 60_000)
    #expect(event.onDevice)
    #expect(event.isFinal)
    #expect(event.confidence == 1)
    #expect(event.soleSpeakerProfile == false)
    #expect(event.ownerSpeakerProfile == false)
    #expect(event.ownerPresenceVerified == false)
}

@Test func transcriptEnvelopeMatchesPythonVoiceContract() throws {
    let event = try #require(
        SpeechTranscriptEvent(
            captureID: UUID(uuidString: "01234567-89AB-CDEF-0123-456789ABCDEF")!,
            sequence: 4,
            text: "Analiza el sistema",
            localeIdentifier: "es-US",
            durationMilliseconds: 1_500,
            isFinal: true,
            confidence: 0.9
        )
    )
    let data = try JSONEncoder().encode(event)
    let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])

    #expect(object["type"] as? String == "speech.transcript")
    #expect(object["schema_version"] as? String == "1.0")
    #expect(object["locale_identifier"] as? String == "es-US")
    #expect(object["is_final"] as? Bool == true)
    #expect(object["on_device"] as? Bool == true)
    #expect(object["audio"] == nil)
}

@Test func transcriptCarriesPairedLocalSpeakerIdentity() throws {
    let event = try #require(
        SpeechTranscriptEvent(
            captureID: UUID(),
            sequence: 7,
            text: "Abre mi calendario",
            localeIdentifier: "es-US",
            durationMilliseconds: 900,
            isFinal: true,
            confidence: 0.91,
            speakerID: "guillermo",
            speakerConfidence: 0.88,
            soleSpeakerProfile: false,
            ownerSpeakerProfile: true,
            ownerPresenceVerified: true
        )
    )
    let data = try JSONEncoder().encode(event)
    let decoded = try SpeechTranscriptEvent.decodeStrictJSON(data)

    #expect(decoded.speakerID == "guillermo")
    #expect(decoded.speakerConfidence == 0.88)
    #expect(!decoded.soleSpeakerProfile)
    #expect(decoded.ownerSpeakerProfile)
    #expect(decoded.ownerPresenceVerified)
}

@Test func transcriptRejectsUnpairedOrUnsafeSpeakerIdentity() {
    #expect(
        SpeechTranscriptEvent(
            captureID: UUID(),
            sequence: 1,
            text: "Hola",
            localeIdentifier: "es-US",
            durationMilliseconds: 500,
            isFinal: true,
            confidence: nil,
            speakerID: "guillermo",
            speakerConfidence: nil
        ) == nil
    )
    #expect(
        SpeechTranscriptEvent(
            captureID: UUID(),
            sequence: 1,
            text: "Hola",
            localeIdentifier: "es-US",
            durationMilliseconds: 500,
            isFinal: true,
            confidence: nil,
            speakerID: "../../owner",
            speakerConfidence: 0.9
        ) == nil
    )
    #expect(
        SpeechTranscriptEvent(
            captureID: UUID(),
            sequence: 1,
            text: "Hola",
            localeIdentifier: "es-US",
            durationMilliseconds: 500,
            isFinal: true,
            confidence: nil,
            ownerSpeakerProfile: true
        ) == nil
    )
    #expect(
        SpeechTranscriptEvent(
            captureID: UUID(),
            sequence: 1,
            text: "Hola",
            localeIdentifier: "es-US",
            durationMilliseconds: 500,
            isFinal: true,
            confidence: nil,
            speakerID: "guillermo",
            speakerConfidence: 0.9,
            ownerPresenceVerified: true
        ) == nil
    )
}

@Test func speechStatusDoesNotContainTranscriptOrAudio() throws {
    let status = SpeechStatusEvent(
        state: "capability",
        microphonePermission: .denied,
        speechPermission: .denied,
        localeIdentifier: "es-US",
        localeSupported: true,
        recognizerAvailable: true,
        onDeviceAvailable: false
    )
    let data = try JSONEncoder().encode(status)
    let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])

    #expect(status.ready == false)
    #expect(object["microphone_permission"] as? String == "denied")
    #expect(object["speech_permission"] as? String == "denied")
    #expect(object["transcript"] == nil)
    #expect(object["audio"] == nil)
}

@Test func transcriberRejectsUnboundedCaptureBeforeCheckingPermissions() {
    #expect(throws: LocalSpeechTranscriberError.invalidConfiguration) {
        try LocalSpeechTranscriber().run(
            durationSeconds: 61,
            intervalMilliseconds: 50,
            localeIdentifier: "es-US"
        )
    }
}
