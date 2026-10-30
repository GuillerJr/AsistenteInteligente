import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

@MainActor private final class CaptureModeCancellation: LocalVoiceCancellation { func cancel() {} }
@MainActor private final class CaptureModeDriver: LocalVoiceDriver {
    var receive: (@MainActor (LocalRecognitionEvent) -> Void)?
    func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation {
        completion(.init(speech: .authorized, microphone: .authorized)); return CaptureModeCancellation()
    }
    func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation {
        completion(.success(())); return CaptureModeCancellation()
    }
    func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws { self.receive = receive }
    func finishRecognition() {}
    func cancelRecognition() {}
    func localVoices(localeIdentifier: String) -> [LocalVoiceChoice] { [] }
    func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws {}
    func stopPlayback() {}
}

final class LocalVoiceCaptureModeIntegrationTests: XCTestCase {
    @MainActor private func fixture() throws -> (ChatStore, CaptureModeDriver) {
        let driver = CaptureModeDriver(), voice = LocalVoiceController(driver: driver)
        let store = ChatStore(voice: voice)
        let session = try XCTUnwrap(ChatSession(.object([
            "session_id": .string(String(repeating: "a", count: 32)), "temporary": .bool(true),
            "title": .string("Prueba sintética de activación"), "archived": .bool(false),
            "agent_id": .string("jarvis"), "parent_session_id": .null,
        ])))
        store.sessions = [session]; store.selectedID = session.id; store.ready = true; store.connected = true
        store.draft = "BORRADOR ACTIVACIÓN"
        voice.preferences.mode = .wakePhrase; voice.preferences.wakePhrase = "Jarvis"
        return (store, driver)
    }
    @MainActor func testSelectedWakePreferenceFlowsThroughChatStoreCallbackWithoutPreTriggerPrefixOrSend() throws {
        let (store, driver) = try fixture()
        defer { store.voice.cancel(); store.runtime.retire() }
        XCTAssertTrue(store.canUseVoice)
        store.startDictation()
        XCTAssertEqual(store.voice.activeCaptureMode, .wakePhrase)
        driver.receive?(.partial("Esto se descarta antes de activar"))
        XCTAssertEqual(store.draft, "BORRADOR ACTIVACIÓN")
        driver.receive?(.partial("Esto se descarta, Jarvis el marcador es 42"))
        XCTAssertEqual(store.voice.partialText, "el marcador es 42")
        store.voice.stopDictation()
        driver.receive?(.final("Esto se descarta, Jarvis el marcador es 42"))
        XCTAssertEqual(store.draft, "BORRADOR ACTIVACIÓN\nel marcador es 42")
        XCTAssertTrue(store.dictationNotice?.contains("Frase de activación detectada") == true)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeMatched, true)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.captureMode, .wakePhrase)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.matchCount, 1)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.triggerCharacterOffset, 18)
        XCTAssertTrue(store.messages.isEmpty); XCTAssertNil(store.activeRunID)
    }
    @MainActor func testRepeatedHypothesesUpdateRAMProjectionAndKeepALegitimateLaterMentionOnlyOnce() throws {
        let (store, driver) = try fixture()
        defer { store.voice.cancel(); store.runtime.retire() }
        store.startDictation()
        driver.receive?(.partial("Antes. Jarvis abre"))
        driver.receive?(.partial("Antes. Jarvis abre la carpeta Jarvis."))
        driver.receive?(.partial("Antes. Jarvis abre la carpeta Jarvis."))
        store.voice.stopDictation()
        driver.receive?(.final("Antes. Jarvis abre la carpeta Jarvis."))
        XCTAssertEqual(store.draft, "BORRADOR ACTIVACIÓN\nabre la carpeta Jarvis.")
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.matchCount, 2)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.suffixSHA256,
                       "8770f66163b45e6d449d18c7ff8d1fe17c4bb83967ec29df4376c24f46317bb6")
        XCTAssertTrue(store.messages.isEmpty); XCTAssertNil(store.activeRunID)
        store.startDictation(); XCTAssertNil(store.voice.lastCaptureReceipt)
        driver.receive?(.partial("Sin activación")); store.stopVoice()
        XCTAssertNil(store.voice.lastCaptureReceipt?.wakeProjection)
    }
    @MainActor func testSegmentedSDKStyleStreamCountsBothTriggersWithoutSilentlyRecuttingThePayload() throws {
        let (store, driver) = try fixture()
        defer { store.voice.cancel(); store.runtime.retire() }
        store.startDictation()
        var segments = LocalSpeechSegments()
        driver.receive?(.partial(try segments.accept(text: "Jarvis.", start: 0, end: 1, isFinal: true)))
        driver.receive?(.partial(try segments.accept(text: "Esto se descarta Jarvis, el marcador es 42.", start: 1, end: 4, isFinal: false)))
        driver.receive?(.partial(try segments.accept(text: "Esto se descarta Jarvis, el marcador es 42.", start: 1, end: 4, isFinal: true)))
        store.voice.stopDictation(); driver.receive?(.final(segments.text))
        XCTAssertEqual(store.draft, "BORRADOR ACTIVACIÓN\nEsto se descarta Jarvis, el marcador es 42.")
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.matchCount, 2)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.discardedPrefixCharacters, 8)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeProjection?.suffixSHA256,
                       "5db2e269d767f02a540e46a26f0794e31be5f2c1d22dc1fc6e09b65af8782ce9")
        XCTAssertTrue(store.messages.isEmpty); XCTAssertNil(store.activeRunID)
    }
    @MainActor func testCancelFromChatStoreNeverPublishesAmbientTextOrChangesSelectedWakePreference() throws {
        let (store, driver) = try fixture(), preferences = store.voice.preferences
        defer { store.voice.cancel(); store.runtime.retire() }
        store.startDictation(); driver.receive?(.partial("Esto se descarta sin activación"))
        store.stopVoice()
        XCTAssertEqual(store.draft, "BORRADOR ACTIVACIÓN"); XCTAssertEqual(store.voice.preferences, preferences)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.outcome, .cancelled)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.captureMode, .wakePhrase)
        XCTAssertEqual(store.voice.lastCaptureReceipt?.wakeMatched, false)
        XCTAssertTrue(store.messages.isEmpty); XCTAssertNil(store.activeRunID)
    }
}
