import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class CodexVoiceReadinessTests: XCTestCase {
    private func connectedChat(voice: IPCSpeechOutputState = .unconfigured) -> MenuBarModel {
        let model = MenuBarModel()
        model.daemonState = .online
        model.securityState = .intact
        model.providerState = .configured
        model.microphonePermission = .authorized
        model.speechPermission = .authorized
        model.screenCaptureAuthorized = true
        model.voiceOutputState = voice
        model.wakeWordOptedIn = false
        return model
    }

    func testConfiguredChatRemainsAvailableWithoutClaimingVoiceOrWakeWordReady() {
        let model = connectedChat()
        XCTAssertTrue(model.hybridBrainReady)
        XCTAssertTrue(model.canSendChatMessage)
        XCTAssertFalse(model.canStartVoiceTurn)
        XCTAssertFalse(model.canStartScreenTurn)
        XCTAssertFalse(model.wakeWordRuntimeAvailable)
        XCTAssertEqual(model.voiceOutputState.title, "Voz sin configurar")
        XCTAssertTrue(model.preflightFailureMessage().contains("chat por escrito"))
        model.voiceOutputState = .ready
        XCTAssertTrue(model.canStartVoiceTurn)
        XCTAssertTrue(model.canStartScreenTurn)
        XCTAssertTrue(model.wakeWordRuntimeAvailable)
        XCTAssertTrue(model.canSendChatMessage)
    }

    func testKnownMissingVoiceNeverStartsPlaybackOrReportsSpokenCompletion() {
        let model = connectedChat()
        var completed = false
        model.speakWithWakeWordIsolation("Respuesta sintética") { completed = true }
        XCTAssertFalse(completed)
        XCTAssertFalse(model.speechOutput.isActive)
        XCTAssertEqual(model.voiceState, .failed)
        XCTAssertEqual(model.lastVoiceFailureCode, "speech_provider_unconfigured")
        XCTAssertEqual(model.voiceFailureTitle, "Voz sin configurar")
        XCTAssertEqual(model.voiceOutputText, "Respuesta sintética")
        XCTAssertTrue(model.canSendChatMessage)
        XCTAssertNil(model.pendingRecovery, "Falta de audio no debe proponer repetir acciones")
    }

    func testStreamWithoutVoiceKeepsTheExistingJobAndFinalTextWithoutStartingAudio() {
        let model = connectedChat()
        let job = UUID()
        model.activeJobID = job
        model.voiceState = .processing
        XCTAssertTrue(model.consumeStreamSnapshot("Primera parte de una respuesta sintética. "))
        XCTAssertFalse(model.speechStreamOpen)
        XCTAssertFalse(model.speechOutput.isActive)
        XCTAssertEqual(model.activeJobID, job)
        XCTAssertEqual(model.voiceState, .processing)
        model.speakCompletedResult("Primera parte de una respuesta sintética. Resultado final.")
        XCTAssertEqual(model.activeJobID, job)
        XCTAssertEqual(model.voiceOutputText, "Primera parte de una respuesta sintética. Resultado final.")
        XCTAssertEqual(model.lastVoiceFailureCode, "speech_provider_unconfigured")
        XCTAssertFalse(model.speechOutput.isActive)
        XCTAssertNil(model.pendingRecovery)
    }

    func testUnavailableVoiceAlsoFailsClosedAndPreservesChat() {
        let model = connectedChat(voice: .unavailable)
        XCTAssertFalse(model.canStartVoiceTurn)
        model.speakCompletedResult("Respuesta de texto")
        XCTAssertEqual(model.lastVoiceFailureCode, "speech_output_unavailable")
        XCTAssertEqual(model.voiceOutputText, "Respuesta de texto")
        XCTAssertFalse(model.speechOutput.isActive)
        XCTAssertTrue(model.canSendChatMessage)
    }
}
