import CryptoKit
import DesktopCore
import Foundation
import XCTest

final class LocalAudioTranscriptionTests: XCTestCase {
    private let data = Data("SYNTHETIC AUDIO BYTES".utf8)
    private var sourceDigest: String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    func testApprovedBytesAreBoundToTheirDigestBeforeDecoderOrPermissionAccess() throws {
        XCTAssertNoThrow(try LocalAudioTranscriptionPolicy.validateSource(data: data, name: "prueba.wav", mimeType: "audio/wav", expectedSHA256: sourceDigest))
        XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateSource(data: data, name: "prueba.wav", mimeType: "audio/wav", expectedSHA256: String(repeating: "0", count: 64))) {
            XCTAssertEqual($0 as? LocalFileSpeechIssue, .sourceChanged)
        }
        XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateSource(data: Data(), name: "vacío.wav", mimeType: "audio/wav", expectedSHA256: sourceDigest))
        XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateSource(data: Data(repeating: 0, count: LocalAudioTranscriptionPolicy.maximumSourceBytes + 1), name: "grande.wav", mimeType: "audio/wav", expectedSHA256: sourceDigest))
        for name in ["", "/private/file.wav", "..\\private.wav", "audio\n.wav"] {
            XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateSource(data: data, name: name, mimeType: "audio/wav", expectedSHA256: sourceDigest))
        }
        for mime in ["text/html", "application/javascript", "https://external.example/audio", "audio/wav; url=remote"] {
            XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateSource(data: data, name: "prueba.wav", mimeType: mime, expectedSHA256: sourceDigest))
        }
    }

    func testDurationAndTranscriptBudgetsRejectInsteadOfPublishingTruncatedSuccess() throws {
        XCTAssertNoThrow(try LocalAudioTranscriptionPolicy.validateDuration(120))
        for duration in [Double.nan, .infinity, -1, 0, 120.001] {
            XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateDuration(duration))
        }
        XCTAssertNoThrow(try LocalAudioTranscriptionPolicy.validateText(String(repeating: "Ω", count: 8000)))
        XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateText(String(repeating: "Ω", count: 8001))) {
            XCTAssertEqual($0 as? LocalFileSpeechIssue, .textLimit)
        }
        for text in ["", " \n", "text\0suffix"] { XCTAssertThrowsError(try LocalAudioTranscriptionPolicy.validateText(text)) }
    }

    func testTranscriptDescribesLocalTextOnlyAndKeepsExactSourceIdentity() throws {
        let result = try LocalAudioTranscript(text: "MARCADOR AUDIO LOCAL", localeIdentifier: "es-ES", durationSeconds: 2.5, sourceSHA256: sourceDigest)
        XCTAssertTrue(result.onDevice); XCTAssertFalse(result.audioSentToProvider)
        XCTAssertEqual(result.sourceSHA256, sourceDigest); XCTAssertEqual(result.durationSeconds, 2.5)
        XCTAssertEqual(result.text, "MARCADOR AUDIO LOCAL")
        XCTAssertThrowsError(try LocalAudioTranscript(text: "texto", localeIdentifier: "invalid/locale", durationSeconds: 1, sourceSHA256: sourceDigest))
        XCTAssertThrowsError(try LocalAudioTranscript(text: "texto", localeIdentifier: "es-ES", durationSeconds: 1, sourceSHA256: "untrusted"))
    }

    func testWakePolicyUsesWordsAndPreservesUnicodeSuffixWithoutPretriggerContent() {
        XCTAssertEqual(LocalWakePhrasePolicy.reviewedSuffix(in: "No conservar. ¡HÓLA, JARVIS! añade Ω y 🟢.", phrase: "Hola Jarvis"), "añade Ω y 🟢.")
        XCTAssertNil(LocalWakePhrasePolicy.reviewedSuffix(in: "Jarvisito no activa", phrase: "Jarvis"))
        XCTAssertNil(LocalWakePhrasePolicy.reviewedSuffix(in: "texto", phrase: ""))
        XCTAssertEqual(LocalWakePhrasePolicy.reviewedSuffix(in: "Jarvis", phrase: "Jarvis"), "")
        XCTAssertEqual(LocalWakePhrasePolicy.reviewedSuffix(in: "Jarvis: escribe «hola»!!!  ", phrase: "Jarvis"), "escribe «hola»!!!")
        XCTAssertEqual(LocalWakePhrasePolicy.reviewedSuffix(in: "Jarvis, ...", phrase: "Jarvis"), "")
        XCTAssertFalse(LocalWakePhrasePolicy.isValid("uno dos tres cuatro cinco"))
    }

    func testWakeProjectionMetadataIdentifiesTheExactSingleTriggerSuffix() throws {
        let projection = try XCTUnwrap(LocalWakePhrasePolicy.reviewedProjection(
            in: "Esto se descarta Jarvis, el marcador es 42.", phrase: "Jarvis"))
        XCTAssertEqual(projection.text, "el marcador es 42.")
        XCTAssertEqual(projection.metadata.matchCount, 1)
        XCTAssertEqual(projection.metadata.triggerCharacterOffset, 17)
        XCTAssertEqual(projection.metadata.discardedPrefixCharacters, 25)
        XCTAssertEqual(projection.metadata.suffixUTF8Bytes, 18)
        XCTAssertEqual(projection.metadata.suffixSHA256, "f21f84e9c883ca343603b529e2bd890333a9a6ecd83b1073cc8c9700f5856288")
    }

    func testWakeProjectionCountsLaterMentionsWithoutBlockingOrChoosingTheLastOne() throws {
        let projection = try XCTUnwrap(LocalWakePhrasePolicy.reviewedProjection(
            in: "🟢 Esto se descarta. ¡JÁRVIS!, abre la carpeta Jarvis.", phrase: "Jarvis"))
        XCTAssertEqual(projection.text, "abre la carpeta Jarvis.")
        XCTAssertEqual(projection.metadata.matchCount, 2)
        XCTAssertEqual(projection.metadata.triggerCharacterOffset, 21)
        XCTAssertEqual(projection.metadata.discardedPrefixCharacters, 30)
        XCTAssertEqual(projection.metadata.suffixUTF8Bytes, 23)
        XCTAssertEqual(projection.metadata.suffixSHA256, "8770f66163b45e6d449d18c7ff8d1fe17c4bb83967ec29df4376c24f46317bb6")
        let description = String(describing: projection.metadata)
        XCTAssertFalse(description.contains("Esto se descarta")); XCTAssertFalse(description.contains("abre la carpeta"))
    }

    func testAnEarlierRecognizedTriggerCanExplainAnIntactLaterPhraseWithoutProvingItWasSpokenTwice() throws {
        let projection = try XCTUnwrap(LocalWakePhrasePolicy.reviewedProjection(
            in: "Jarvis. Esto se descarta Jarvis, el marcador es 42.", phrase: "Jarvis"))
        XCTAssertEqual(projection.text, "Esto se descarta Jarvis, el marcador es 42.")
        XCTAssertEqual(projection.metadata.matchCount, 2)
        XCTAssertEqual(projection.metadata.triggerCharacterOffset, 0)
        XCTAssertEqual(projection.metadata.discardedPrefixCharacters, 8)
        XCTAssertEqual(projection.metadata.suffixUTF8Bytes, 43)
        XCTAssertEqual(projection.metadata.suffixSHA256, "5db2e269d767f02a540e46a26f0794e31be5f2c1d22dc1fc6e09b65af8782ce9")
    }
}
