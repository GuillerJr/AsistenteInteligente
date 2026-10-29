import DesktopCore
import Foundation
import XCTest

@MainActor private final class TalkCancellation: LocalVoiceCancellation {
    var active = true
    func cancel() { active = false }
}
@MainActor private final class TalkDeadline: LocalVoiceCancellation {
    let action: @MainActor () -> Void
    let seconds: TimeInterval
    var active = true
    init(_ seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) { self.seconds = seconds; self.action = action }
    func cancel() { active = false }
    func fire(evenIfCancelled: Bool = false) { if active || evenIfCancelled { action() } }
}
@MainActor private final class TalkClock: LocalVoiceClock {
    var entries: [TalkDeadline] = []
    func schedule(after seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any LocalVoiceCancellation {
        let value = TalkDeadline(seconds, action); entries.append(value); return value
    }
}
@MainActor private final class TalkDriver: LocalVoiceDriver {
    var recognition: [@MainActor (LocalRecognitionEvent) -> Void] = []
    var playback: [@MainActor (LocalPlaybackEvent) -> Void] = []
    var spoken: [String] = []
    var permissionRequests = 0
    var localAvailable = true
    func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation {
        permissionRequests += 1
        completion(.init(speech: .authorized, microphone: .authorized)); return TalkCancellation()
    }
    func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation {
        completion(localAvailable ? .success(()) : .failure(.onDeviceUnavailable)); return TalkCancellation()
    }
    func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws { recognition.append(receive) }
    func finishRecognition() {}
    func cancelRecognition() {}
    func localVoices(localeIdentifier: String) -> [LocalVoiceChoice] { [.init(id: "com.apple.voice.synthetic", name: "Sintética", language: "es-ES")] }
    func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws { spoken.append(text); playback.append(receive) }
    func stopPlayback() {}
}
@MainActor private final class TalkHost {
    var context: LocalVoiceConversationContext? = .init(contextID: "synthetic:one", revision: "runtime-one:revision-7")
    var sent: [(String, LocalVoiceConversationContext)] = []
    var cancelContexts: [LocalVoiceConversationContext] = []
    var cancelConfirmed = true
    var holdReply = false
    var pending: CheckedContinuation<LocalVoiceConversationReply, Error>?
    var replies: [LocalVoiceConversationReply] = (0..<3).map { index in
        .init(runID: String(repeating: String(index + 1), count: 32), status: "completed", outcome: "known", answer: "Respuesta sintética \(index + 1)")
    }
    func submit(_ text: String, _ context: LocalVoiceConversationContext) async throws -> LocalVoiceConversationReply {
        sent.append((text, context))
        if holdReply { return try await withCheckedThrowingContinuation { pending = $0 } }
        return replies.removeFirst()
    }
    func cancel(_ context: LocalVoiceConversationContext) async -> Bool {
        cancelContexts.append(context)
        if cancelConfirmed { pending?.resume(throwing: CancellationError()); pending = nil }
        return cancelConfirmed
    }
}
@MainActor private struct TalkFixture {
    let driver = TalkDriver()
    let clock = TalkClock()
    let captureClock = TalkClock()
    let host = TalkHost()
    let voice: LocalVoiceController
    let talk: LocalVoiceConversationController
    init() {
        let voice = LocalVoiceController(driver: driver, clock: captureClock)
        self.voice = voice
        let host = host
        let controller = LocalVoiceConversationController(voice: voice, clock: clock,
            currentContext: { host.context }, submit: { try await host.submit($0, $1) },
            cancelRun: { await host.cancel($0) })
        talk = controller
        voice.onTranscript = { [weak controller] value in _ = controller?.acceptTranscript(value) }
        voice.onCaptureCompletion = { [weak controller] in controller?.captureCompleted($0) }
        voice.onPlaybackCompletion = { [weak controller] in controller?.playbackCompleted($0) }
    }
    func advance() async { for _ in 0..<50 { await Task.yield() } }
}

final class LocalVoiceConversationTests: XCTestCase {
    @MainActor func testInitializationNeverCapturesOrSubmitsAndStartBindsExactContext() async {
        let f = TalkFixture()
        XCTAssertEqual(f.talk.phase, .idle); XCTAssertEqual(f.driver.permissionRequests, 0); XCTAssertTrue(f.host.sent.isEmpty)
        XCTAssertFalse(f.talk.start(context: .init(contextID: "other", revision: "revision")))
        XCTAssertEqual(f.driver.permissionRequests, 0)
        XCTAssertTrue(f.talk.start(context: f.host.context!))
        XCTAssertEqual(f.talk.phase, .listening); XCTAssertEqual(f.driver.permissionRequests, 1)
        XCTAssertEqual(f.clock.entries.first?.seconds, 300)
        f.talk.stop(); await f.advance()
        XCTAssertEqual(f.talk.phase, .idle)
    }

    @MainActor func testThreeFinalizedTextTurnsKeepMicrophoneOffUntilPlaybackFinishes() async {
        let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
        for index in 0..<3 {
            f.driver.recognition[index](.final("Texto reconocido \(index)"))
            XCTAssertEqual(f.voice.phase, .idle)
            XCTAssertEqual(f.talk.phase, .waitingForModel)
            await f.advance()
            XCTAssertEqual(f.talk.phase, .speaking); XCTAssertEqual(f.voice.phase, .speaking)
            XCTAssertEqual(f.driver.recognition.count, index + 1, "No input opens while Codex or TTS owns the turn")
            XCTAssertEqual(f.host.sent[index].0, "Texto reconocido \(index)")
            f.driver.playback[index](.finished)
            if index < 2 {
                XCTAssertEqual(f.driver.recognition.count, index + 1, "Playback must be followed by a quiet, cancellable gap")
                XCTAssertEqual(f.clock.entries.last?.seconds, 0.35)
                f.clock.entries.last?.fire()
            }
            await f.advance()
        }
        XCTAssertEqual(f.host.sent.count, 3); XCTAssertEqual(f.driver.recognition.count, 3)
        XCTAssertEqual(f.talk.completedTurns, 3); XCTAssertEqual(f.talk.phase, .idle)
        XCTAssertEqual(f.talk.stopReason, .turnLimit)
    }

    @MainActor func testPartialAtDeadlineIsReviewMaterialAndNeverSent() async {
        let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
        let context = f.host.context!
        f.driver.recognition[0](.partial("Texto parcial sin envío"))
        f.voice.stopDictation(); f.captureClock.entries.last?.fire()
        await f.advance()
        XCTAssertTrue(f.host.sent.isEmpty); XCTAssertTrue(f.driver.spoken.isEmpty)
        XCTAssertEqual(f.talk.stopReason, .partialTranscript)
        XCTAssertNil(f.talk.takePartialForReview(context: .init(contextID: "other", revision: "revision")))
        XCTAssertEqual(f.talk.takePartialForReview(context: context)?.text, "Texto parcial sin envío")
        XCTAssertNil(f.talk.takePartialForReview(context: context))
    }

    @MainActor func testPendingToolApprovalCancelsExactRunAndNeverRearms() async {
        let f = TalkFixture(); f.host.holdReply = true
        XCTAssertTrue(f.talk.start(context: f.host.context!))
        f.driver.recognition[0](.final("Petición que necesita aprobación")); await f.advance()
        f.talk.approvalBecamePending(); await f.advance()
        XCTAssertEqual(f.talk.phase, .idle); XCTAssertEqual(f.talk.stopReason, .approvalRequired)
        XCTAssertEqual(f.host.cancelContexts, [f.host.context!]); XCTAssertTrue(f.driver.spoken.isEmpty)
        XCTAssertEqual(f.driver.recognition.count, 1)
    }

    @MainActor func testUnknownReceiptDuplicateRunAndPlaybackFailureCannotStartAnotherCapture() async {
        for scenario in 0..<3 {
            let f = TalkFixture()
            if scenario == 0 { f.host.replies = [.init(runID: String(repeating: "a", count: 32), status: "interrupted", outcome: "unknown", answer: "No demostrar éxito")] }
            XCTAssertTrue(f.talk.start(context: f.host.context!))
            f.driver.recognition[0](.final("Primera frase")); await f.advance()
            if scenario == 1 {
                f.host.replies[0] = .init(runID: f.talk.lastRunID!, status: "completed", outcome: "known", answer: "ACK repetido")
                f.driver.playback[0](.finished); f.clock.entries.last?.fire()
                f.driver.recognition[1](.final("Segunda frase")); await f.advance()
                XCTAssertEqual(f.driver.spoken.count, 1); XCTAssertEqual(f.driver.recognition.count, 2)
            } else if scenario == 2 {
                f.driver.playback[0](.failed); await f.advance()
                XCTAssertEqual(f.driver.recognition.count, 1)
                XCTAssertEqual(f.talk.stopReason, .playbackFailed)
            }
            XCTAssertEqual(f.talk.phase, .idle)
            if scenario != 2 { XCTAssertEqual(f.talk.stopReason, .runUnconfirmed) }
        }
    }

    @MainActor func testContextChangeAndOldPlaybackCannotSpeakOrCaptureInAnotherScope() async {
        let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
        f.driver.recognition[0](.final("Sólo en el ámbito original")); await f.advance()
        let oldPlayback = f.driver.playback[0]
        f.host.context = .init(contextID: "synthetic:two", revision: "runtime-two:revision-8")
        f.talk.validateContext(); await f.advance()
        oldPlayback(.finished); await f.advance()
        XCTAssertEqual(f.talk.stopReason, .contextChanged)
        XCTAssertEqual(f.driver.recognition.count, 1)
        XCTAssertEqual(f.host.cancelContexts.first?.contextID, "synthetic:one")
    }

    @MainActor func testUnconfirmedCancellationRetainsOwnerUntilExplicitRetry() async {
        let f = TalkFixture(); f.host.holdReply = true; f.host.cancelConfirmed = false
        XCTAssertTrue(f.talk.start(context: f.host.context!))
        f.driver.recognition[0](.final("Cancelar durante el modelo")); await f.advance()
        f.talk.stop(); await f.advance()
        XCTAssertEqual(f.talk.phase, .stopping); XCTAssertEqual(f.talk.stopReason, .cleanupPending)
        XCTAssertFalse(f.talk.start(context: f.host.context!)); XCTAssertEqual(f.driver.recognition.count, 1)
        f.host.cancelConfirmed = true; f.talk.retryCleanup(); await f.advance()
        XCTAssertEqual(f.talk.phase, .idle); XCTAssertNil(f.talk.activeContext)
        XCTAssertEqual(f.host.cancelContexts.count, 2)
    }

    @MainActor func testGlobalDeadlineAndRetiredCaptureNeverReplayTextOrStopNewContext() async {
        let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
        let oldID = f.voice.captureIdentifier!, oldDeadline = f.clock.entries[0]
        oldDeadline.fire(); await f.advance()
        XCTAssertEqual(f.talk.stopReason, .timeLimit); XCTAssertTrue(f.host.sent.isEmpty)
        XCTAssertTrue(f.talk.start(context: f.host.context!))
        let old = LocalVoiceTranscript(contextID: f.host.context!.contextID, text: "No repetir", completion: .recognizedFinal, captureID: oldID)
        XCTAssertTrue(f.talk.acceptTranscript(old)); oldDeadline.fire(evenIfCancelled: true)
        XCTAssertEqual(f.talk.phase, .listening); XCTAssertTrue(f.host.sent.isEmpty)
        f.talk.stop(); await f.advance()
        let ordinary = LocalVoiceTranscript(contextID: f.host.context!.contextID, text: "Dictado manual nuevo", completion: .recognizedFinal, captureID: UUID())
        XCTAssertFalse(f.talk.acceptTranscript(ordinary), "A finished Talk mode must not swallow later ordinary dictation")
    }

    @MainActor func testEmptyOrRevokedCaptureStopsWithoutACloudRequest() async {
        for event in [LocalRecognitionEvent.final(" \n"), .issue(.microphoneDenied), .issue(.inputChanged)] {
            let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
            f.driver.recognition[0](event); await f.advance()
            XCTAssertEqual(f.talk.phase, .idle); XCTAssertEqual(f.talk.stopReason, .captureFailed)
            XCTAssertTrue(f.host.sent.isEmpty); XCTAssertTrue(f.driver.spoken.isEmpty)
        }
    }

    @MainActor func testPostPlaybackDelayCannotRearmAfterStopOrContextChange() async {
        let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
        f.driver.recognition[0](.final("Pregunta sintética")); await f.advance()
        f.driver.playback[0](.finished); let delayed = f.clock.entries.last!
        XCTAssertEqual(f.voice.phase, .idle); XCTAssertEqual(f.driver.recognition.count, 1)
        f.host.context = .init(contextID: "another", revision: "revision-9")
        delayed.fire(); await f.advance()
        XCTAssertEqual(f.talk.stopReason, .contextChanged); XCTAssertEqual(f.driver.recognition.count, 1)
        XCTAssertTrue(f.talk.start(context: f.host.context!))
        delayed.fire(evenIfCancelled: true)
        XCTAssertEqual(f.talk.phase, .listening); XCTAssertEqual(f.driver.recognition.count, 2)
        f.talk.stop(); await f.advance()
    }

    @MainActor func testRecognizedEchoIsHeldForReviewAndNeverSentAgain() async {
        let f = TalkFixture(); XCTAssertTrue(f.talk.start(context: f.host.context!))
        f.driver.recognition[0](.final("Pregunta")); await f.advance()
        let spoken = f.driver.spoken[0]
        f.driver.playback[0](.finished); f.clock.entries.last?.fire()
        f.driver.recognition[1](.final(spoken.uppercased() + ".")); await f.advance()
        XCTAssertEqual(f.talk.stopReason, .possibleEcho)
        XCTAssertEqual(f.host.sent.count, 1); XCTAssertEqual(f.driver.spoken.count, 1)
        XCTAssertEqual(f.talk.takePartialForReview(context: f.host.context!)?.text, spoken.uppercased() + ".")
    }
}
