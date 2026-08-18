import Foundation
import Testing
@testable import AegisAudioCore

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
