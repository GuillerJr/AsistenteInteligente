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
