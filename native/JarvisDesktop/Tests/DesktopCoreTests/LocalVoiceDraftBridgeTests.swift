import Foundation
import XCTest
@testable import DesktopCore

@MainActor private final class BridgeVoiceCancellation: LocalVoiceCancellation { func cancel() {} }
@MainActor private final class BridgeVoiceDriver: LocalVoiceDriver {
    var ready: [@MainActor (Result<Void, LocalVoiceIssue>) -> Void] = []
    var recognition: [@MainActor (LocalRecognitionEvent) -> Void] = []
    func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation {
        completion(.init(speech: .authorized, microphone: .authorized)); return BridgeVoiceCancellation()
    }
    func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation {
        ready.append(completion); return BridgeVoiceCancellation()
    }
    func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws { recognition.append(receive) }
    func finishRecognition() {}
    func cancelRecognition() {}
    func localVoices(localeIdentifier: String) -> [LocalVoiceChoice] { [] }
    func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws {}
    func stopPlayback() {}
}

final class LocalVoiceDraftBridgeTests: XCTestCase {
    func testDictationPreservesExistingDraftAndNeverCrossesConversationOrTemporaryMode() {
        let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        let transcript = LocalVoiceTranscript(contextID: LocalVoiceDraftBridge.contextID(scope),
                                               text: "Dictado pendiente de revisar", completion: .recognizedFinal)
        XCTAssertEqual(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "Texto escrito", eligible: true),
                       "Texto escrito\nDictado pendiente de revisar")
        let another = SessionScope(sessionID: String(repeating: "b", count: 32), temporary: false)
        let temporary = SessionScope(sessionID: scope.sessionID, temporary: true)
        for invalid in [another, temporary] {
            XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: invalid, existing: "Conservar", eligible: true))
        }
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: nil, existing: "Conservar", eligible: true))
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "Conservar", eligible: false))
    }

    func testPartialTextRemainsReviewMaterialAndUTF8BudgetRejectsWithoutTruncation() {
        let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
        let transcript = LocalVoiceTranscript(contextID: LocalVoiceDraftBridge.contextID(scope), text: "Ω", completion: .partialAtStop)
        XCTAssertTrue(transcript.requiresReview)
        XCTAssertEqual(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "x\n", eligible: true, maximumBytes: 4), "x\nΩ")
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "x\n", eligible: true, maximumBytes: 3))
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: String(repeating: "x", count: 32000), eligible: true))
    }

    @MainActor func testCancelledReadinessCannotAppendToAnotherDraftAndFinalPreservesTypedText() {
        let driver = BridgeVoiceDriver()
        let voice = LocalVoiceController(driver: driver)
        var selected = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        var draft = "BORRADOR BASE"
        voice.onTranscript = { transcript in
            if let value = LocalVoiceDraftBridge.append(transcript, scope: selected, existing: draft, eligible: true) { draft = value }
        }
        voice.startDictation(contextID: LocalVoiceDraftBridge.contextID(selected))
        voice.cancel()
        selected = SessionScope(sessionID: String(repeating: "b", count: 32), temporary: false)
        voice.startDictation(contextID: LocalVoiceDraftBridge.contextID(selected))
        driver.ready[0](.success(()))
        XCTAssertTrue(driver.recognition.isEmpty)
        XCTAssertEqual(draft, "BORRADOR BASE")
        driver.ready[1](.success(()))
        driver.recognition[0](.partial("Primera frase."))
        driver.recognition[0](.partial("Primera frase. Segunda frase."))
        XCTAssertEqual(draft, "BORRADOR BASE")
        voice.stopDictation()
        driver.recognition[0](.final("Primera frase. Segunda frase."))
        XCTAssertEqual(draft, "BORRADOR BASE\nPrimera frase. Segunda frase.")
        XCTAssertEqual(voice.phase, .idle)
    }

    @MainActor func testMissingInstalledAssetLeavesTheOriginalDraftUnchanged() {
        let driver = BridgeVoiceDriver()
        let controller = LocalVoiceController(driver: driver)
        let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
        var draft = "BORRADOR BASE"
        controller.onTranscript = { transcript in
            if let value = LocalVoiceDraftBridge.append(transcript, scope: scope, existing: draft, eligible: true) { draft = value }
        }
        controller.startDictation(contextID: LocalVoiceDraftBridge.contextID(scope))
        driver.ready[0](.failure(.onDeviceUnavailable))
        XCTAssertEqual(controller.issue, .onDeviceUnavailable)
        XCTAssertTrue(driver.recognition.isEmpty)
        XCTAssertEqual(draft, "BORRADOR BASE")
    }
}
