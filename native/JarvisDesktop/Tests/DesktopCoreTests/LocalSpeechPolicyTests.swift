import DesktopCore
import XCTest

final class LocalSpeechPolicyTests: XCTestCase {
    func testInstalledModernSpanishDoesNotRequireTheLegacyAssetOrAnotherRegion() {
        XCTAssertTrue(LocalSpeechPolicy.hasExactInstalledLocale("es-ES", supported: ["es-ES", "es-MX"], installed: ["es-ES"]))
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale("es-ES", supported: ["es-ES", "es-MX"], installed: ["es-MX"]))
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale("es-ES", supported: ["es-MX"], installed: ["es-ES"]))
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale("es-ES", supported: ["es-ES"], installed: []))
    }

    func testLocaleNormalizationChangesOnlyCaseAndSeparator() {
        XCTAssertTrue(LocalSpeechPolicy.hasExactInstalledLocale("ES_es", supported: ["es-ES"], installed: ["es_ES"]))
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale("es", supported: ["es-ES"], installed: ["es-ES"]))
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale("es-419", supported: ["es-MX"], installed: ["es-MX"]))
        for invalid in ["", " es-ES", "es-ES\n", "es--ES", "-es", "es-", "es/ES", String(repeating: "a", count: 65)] {
            XCTAssertNil(LocalSpeechPolicy.canonicalLocale(invalid), invalid)
        }
    }

    func testReadinessRecheckRejectsAnInstalledLocaleWithdrawnDuringPreparation() {
        let requested = "es-ES", supported = ["es-ES", "es-MX"]
        XCTAssertTrue(LocalSpeechPolicy.hasExactInstalledLocale(requested, supported: supported, installed: ["es-ES"]))
        // The same prepared language loses eligibility if its asset disappears;
        // an installed different region cannot keep that operation authorized.
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale(requested, supported: supported, installed: ["es-MX"]))
        XCTAssertFalse(LocalSpeechPolicy.hasExactInstalledLocale(requested, supported: [], installed: ["es-ES"]))
    }

    func testPCMQueueAndIndividualBuffersHaveAFiniteBudget() {
        XCTAssertEqual(LocalSpeechPolicy.queuedAudioBuffers * LocalSpeechPolicy.maximumPCMBufferBytes, 524288)
        XCTAssertTrue(LocalSpeechPolicy.acceptsPCM(sampleRate: 48000, channels: 2, frames: 8192, bytes: 65536))
        for bytes in [0, -1, 65537, Int.max] {
            XCTAssertFalse(LocalSpeechPolicy.acceptsPCM(sampleRate: 48000, channels: 2, frames: 1024, bytes: bytes))
        }
        for rate in [Double.nan, .infinity, -1, 0, 96001] {
            XCTAssertFalse(LocalSpeechPolicy.acceptsPCM(sampleRate: rate, channels: 1, frames: 1024, bytes: 4096))
        }
        XCTAssertFalse(LocalSpeechPolicy.acceptsPCM(sampleRate: 48000, channels: 3, frames: 1024, bytes: 4096))
        XCTAssertFalse(LocalSpeechPolicy.acceptsPCM(sampleRate: 48000, channels: 1, frames: 0, bytes: 4096))
        XCTAssertFalse(LocalSpeechPolicy.acceptsPCM(sampleRate: 48000, channels: 1, frames: 8193, bytes: 4096))
    }

    func testVolatileRevisionsReplaceAndFinalSegmentsAccumulateWithoutDuplication() throws {
        var segments = LocalSpeechSegments()
        XCTAssertEqual(try segments.accept(text: "Hola mun", start: 0, end: 1, isFinal: false), "Hola mun")
        XCTAssertEqual(try segments.accept(text: "Hola mundo.", start: 0, end: 1.5, isFinal: false), "Hola mundo.")
        XCTAssertEqual(try segments.accept(text: "Hola mundo.", start: 0, end: 1.5, isFinal: true), "Hola mundo.")
        XCTAssertEqual(try segments.accept(text: "Segunda fra", start: 2, end: 3, isFinal: false), "Hola mundo. Segunda fra")
        XCTAssertEqual(try segments.accept(text: "Segunda frase.", start: 2, end: 3.5, isFinal: true), "Hola mundo. Segunda frase.")
        XCTAssertEqual(try segments.accept(text: "Segunda frase.", start: 2, end: 3.5, isFinal: true), "Hola mundo. Segunda frase.")
    }

    func testAFinalRangeDoesNotDeleteALaterVolatileSegment() throws {
        var segments = LocalSpeechSegments()
        _ = try segments.accept(text: "después", start: 2, end: 3, isFinal: false)
        XCTAssertEqual(try segments.accept(text: "antes", start: 0, end: 1, isFinal: true), "antes después")
    }

    func testOverlappingOrContradictoryFinalRangesFailWithoutDuplicatingText() throws {
        var segments = LocalSpeechSegments()
        _ = try segments.accept(text: "primero", start: 0, end: 2, isFinal: true)
        XCTAssertThrowsError(try segments.accept(text: "otro", start: 1, end: 3, isFinal: true))
        XCTAssertThrowsError(try segments.accept(text: "cambiado", start: 0, end: 2, isFinal: true))
        XCTAssertEqual(segments.text, "primero")
    }

    func testAggregatedUTF8BudgetRejectsWithoutReplacingThePreviouslyValidResult() throws {
        var segments = LocalSpeechSegments(maximumBytes: 9)
        _ = try segments.accept(text: "🟢", start: 0, end: 1, isFinal: true)
        XCTAssertEqual(try segments.accept(text: "🟢", start: 1, end: 2, isFinal: false), "🟢 🟢")
        XCTAssertThrowsError(try segments.accept(text: "🟢🟢", start: 1, end: 2, isFinal: false)) { error in
            XCTAssertEqual(error as? LocalVoiceIssue, .textLimit)
        }
        XCTAssertEqual(segments.text, "🟢 🟢")
    }

    func testInvalidRangesAreNotUsedAsDraftText() {
        for (start, end) in [(Double.nan, 1), (0, Double.infinity), (-1, 1), (2, 1)] {
            var segments = LocalSpeechSegments()
            XCTAssertThrowsError(try segments.accept(text: "no", start: start, end: end, isFinal: true))
            XCTAssertTrue(segments.text.isEmpty)
        }
    }

    func testFileTranscriptionCanDistinguishFinalTextFromAnUnconfirmedTail() throws {
        var segments = LocalSpeechSegments()
        _ = try segments.accept(text: "Frase confirmada.", start: 0, end: 1, isFinal: true)
        _ = try segments.accept(text: "final pendiente", start: 1, end: 2, isFinal: false)
        XCTAssertEqual(segments.finalizedText, "Frase confirmada.")
        XCTAssertTrue(segments.hasUnfinalizedText)
        _ = try segments.accept(text: "Final confirmado.", start: 1, end: 2, isFinal: true)
        XCTAssertFalse(segments.hasUnfinalizedText)
        XCTAssertEqual(segments.finalizedText, "Frase confirmada. Final confirmado.")
    }
}
