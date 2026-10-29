import Foundation
import DesktopCore
import XCTest

@MainActor private final class Cancellation: LocalVoiceCancellation {
    var active = true
    func cancel() { active = false }
}

@MainActor private final class FakeDeadline: LocalVoiceCancellation {
    let seconds: TimeInterval
    let action: @MainActor () -> Void
    var active = true
    init(seconds: TimeInterval, action: @escaping @MainActor () -> Void) { self.seconds = seconds; self.action = action }
    func cancel() { active = false }
    func fire(evenIfCancelled: Bool = false) { if active || evenIfCancelled { action() } }
}

@MainActor private final class FakeClock: LocalVoiceClock {
    var entries: [FakeDeadline] = []
    var activeCount: Int { entries.filter(\.active).count }
    func schedule(after seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any LocalVoiceCancellation {
        let value = FakeDeadline(seconds: seconds, action: action); entries.append(value); return value
    }
}

@MainActor private final class FakeDriver: LocalVoiceDriver {
    var permissionRequests: [(Cancellation, @MainActor (LocalVoicePermissions) -> Void)] = []
    var recognitionReceivers: [@MainActor (LocalRecognitionEvent) -> Void] = []
    var playbackReceivers: [@MainActor (LocalPlaybackEvent) -> Void] = []
    var recognitionLocales: [String] = []
    var playbackTexts: [String] = []
    var selectedVoices: [LocalVoiceChoice] = []
    var voiceQueries: [String] = []
    var availabilityQueries: [String] = []
    var preparations: [(Cancellation, @MainActor (Result<Void, LocalVoiceIssue>) -> Void)] = []
    var deferPreparation = false
    var supportsLocal = true
    var beginRecognitionError: Error?
    var beginPlaybackError: Error?
    var finishCalls = 0
    var cancelCalls = 0
    var stopPlaybackCalls = 0
    var voices = [LocalVoiceChoice(id: "com.apple.voice.fixture", name: "Voz de prueba", language: "es-ES")]
    var inlinePermissions: LocalVoicePermissions?
    var inlineRecognition: LocalRecognitionEvent?
    var inlinePlayback: LocalPlaybackEvent?

    func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation {
        let token = Cancellation(); permissionRequests.append((token, completion))
        if let inlinePermissions { completion(inlinePermissions) }
        return token
    }
    func authorize(_ permissions: LocalVoicePermissions = .init(speech: .authorized, microphone: .authorized), index: Int? = nil) {
        permissionRequests[index ?? (permissionRequests.count - 1)].1(permissions)
    }
    func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation {
        availabilityQueries.append(localeIdentifier)
        let token = Cancellation(); preparations.append((token, completion))
        if !deferPreparation { completion(supportsLocal ? .success(()) : .failure(.onDeviceUnavailable)) }
        return token
    }
    func prepare(_ result: Result<Void, LocalVoiceIssue> = .success(()), index: Int? = nil) {
        preparations[index ?? (preparations.count - 1)].1(result)
    }
    func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws {
        if let beginRecognitionError { throw beginRecognitionError }
        recognitionLocales.append(localeIdentifier); recognitionReceivers.append(receive)
        if let inlineRecognition { receive(inlineRecognition) }
    }
    func finishRecognition() { finishCalls += 1 }
    func cancelRecognition() { cancelCalls += 1 }
    func localVoices(localeIdentifier: String) -> [LocalVoiceChoice] { voiceQueries.append(localeIdentifier); return voices }
    func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws {
        if let beginPlaybackError { throw beginPlaybackError }
        playbackTexts.append(text); selectedVoices.append(voice); playbackReceivers.append(receive)
        if let inlinePlayback { receive(inlinePlayback) }
    }
    func stopPlayback() { stopPlaybackCalls += 1 }
}

@MainActor private final class ResultSink { var values: [LocalVoiceTranscript] = [] }

@MainActor private struct Fixture {
    let driver = FakeDriver()
    let clock = FakeClock()
    let sink = ResultSink()
    let controller: LocalVoiceController
    init(maxBytes: Int = 16000) {
        controller = LocalVoiceController(driver: driver, clock: clock, maxTextBytes: maxBytes)
        controller.onTranscript = { [sink] value in sink.values.append(value) }
    }
    func start(_ context: String = "test-session") {
        controller.startDictation(contextID: context)
        driver.authorize()
    }
}

final class LocalVoiceControllerTests: XCTestCase {
    @MainActor func testInitializationDoesNotQueryPermissionsOrHardwareOrVoices() {
        let f = Fixture()
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertTrue(f.driver.permissionRequests.isEmpty)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        XCTAssertTrue(f.driver.voiceQueries.isEmpty)
        XCTAssertTrue(f.driver.availabilityQueries.isEmpty)
        XCTAssertTrue(f.clock.entries.isEmpty)
        XCTAssertEqual(f.driver.stopPlaybackCalls, 0)
    }

    @MainActor func testExplicitStartWaitsForOwnersPermissionBeforeCapture() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one")
        XCTAssertEqual(f.controller.phase, .authorizing)
        XCTAssertEqual(f.driver.permissionRequests.count, 1)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        f.driver.authorize()
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertEqual(f.driver.recognitionReceivers.count, 1)
    }

    @MainActor func testSpeechDenialDoesNotCaptureAndProducesNoTranscript() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one")
        f.driver.authorize(.init(speech: .denied, microphone: .notDetermined))
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertEqual(f.controller.issue, .speechDenied)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertFalse(f.driver.permissionRequests[0].0.active)
    }

    @MainActor func testMicrophoneDenialDoesNotCapture() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one")
        f.driver.authorize(.init(speech: .authorized, microphone: .restricted))
        XCTAssertEqual(f.controller.issue, .microphoneDenied)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testUnavailableLocalRecognitionHasNoRemoteFallback() {
        let f = Fixture(); f.driver.supportsLocal = false
        f.start()
        XCTAssertEqual(f.controller.issue, .onDeviceUnavailable)
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertEqual(f.driver.availabilityQueries, ["es-ES"])
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        XCTAssertTrue(f.sink.values.isEmpty)
    }

    @MainActor func testReadinessUsesTheOriginalPermissionBudgetAndDoesNotOpenCapture() {
        let f = Fixture(); f.driver.deferPreparation = true
        f.controller.startDictation(contextID: "one")
        let original = f.clock.entries[0]
        f.driver.authorize()
        XCTAssertEqual(f.controller.phase, .authorizing)
        XCTAssertTrue(original.active)
        XCTAssertEqual(f.clock.entries.count, 1)
        XCTAssertTrue(f.driver.recognitionLocales.isEmpty)
        original.fire()
        XCTAssertEqual(f.controller.issue, .authorizationTimeout)
        XCTAssertFalse(f.driver.preparations[0].0.active)
        f.driver.prepare()
        XCTAssertTrue(f.driver.recognitionLocales.isEmpty)
        XCTAssertTrue(f.sink.values.isEmpty)
    }

    @MainActor func testLateReadinessOrOldCleanupCannotStopTheNextContext() {
        let f = Fixture(); f.driver.deferPreparation = true
        f.controller.startDictation(contextID: "old"); f.driver.authorize()
        let oldDeadline = f.clock.entries[0]
        f.controller.cancel()
        let cleanupCount = f.driver.cancelCalls
        f.controller.startDictation(contextID: "new"); f.driver.authorize()
        f.driver.prepare(index: 0)
        oldDeadline.fire(evenIfCancelled: true)
        XCTAssertEqual(f.controller.phase, .authorizing)
        XCTAssertEqual(f.controller.contextID, "new")
        XCTAssertEqual(f.driver.cancelCalls, cleanupCount)
        XCTAssertTrue(f.driver.recognitionLocales.isEmpty)
        f.driver.prepare(index: 1)
        XCTAssertEqual(f.controller.phase, .listening)
        f.driver.prepare(.failure(.onDeviceUnavailable), index: 0)
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertNil(f.controller.issue)
        f.driver.recognitionReceivers[0](.final("sólo nuevo"))
        XCTAssertEqual(f.sink.values.map(\.contextID), ["new"])
    }

    @MainActor func testMissingExactAssetsAndDuplicatePermissionCallbackNeverStartOrRetry() {
        let f = Fixture(); f.driver.deferPreparation = true
        f.controller.startDictation(contextID: "one")
        f.driver.authorize(); f.driver.authorize()
        XCTAssertEqual(f.driver.preparations.count, 1)
        f.driver.prepare(.failure(.onDeviceUnavailable))
        XCTAssertEqual(f.controller.issue, .onDeviceUnavailable)
        XCTAssertEqual(f.driver.availabilityQueries, ["es-ES"])
        XCTAssertTrue(f.driver.recognitionLocales.isEmpty)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testStopDuringPreparationDiscardsReadinessAndLeavesDraftUntouched() {
        let f = Fixture(); f.driver.deferPreparation = true
        f.controller.startDictation(contextID: "one"); f.driver.authorize()
        f.controller.stopDictation()
        f.driver.prepare()
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertTrue(f.driver.recognitionLocales.isEmpty)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertFalse(f.driver.preparations[0].0.active)
    }

    @MainActor func testSegmentUpdatesStayInTheDraftPreviewUntilSessionCompletion() {
        let f = Fixture(); f.start("origin")
        f.driver.recognitionReceivers[0](.partial("Primera frase."))
        f.driver.recognitionReceivers[0](.partial("Primera frase. Segunda frase"))
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertTrue(f.sink.values.isEmpty)
        f.controller.stopDictation()
        f.driver.recognitionReceivers[0](.final("Primera frase. Segunda frase."))
        XCTAssertEqual(f.sink.values.map(\.text), ["Primera frase. Segunda frase."])
        f.driver.recognitionReceivers[0](.final("duplicado"))
        XCTAssertEqual(f.sink.values.count, 1)
    }

    @MainActor func testAudioQueueFailureIsVisibleAndDoesNotPublishPartialAsSuccess() {
        let f = Fixture(); f.start()
        f.driver.recognitionReceivers[0](.partial("parcial no confirmado"))
        f.driver.recognitionReceivers[0](.issue(.captureFailed))
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertEqual(f.controller.issue, .captureFailed)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testFinalTranscriptIsDraftMaterialForItsOriginalContextOnly() {
        let f = Fixture(); f.start("origin-session")
        f.driver.recognitionReceivers[0](.partial("borrador"))
        XCTAssertEqual(f.controller.partialText, "borrador")
        XCTAssertTrue(f.sink.values.isEmpty)
        f.driver.recognitionReceivers[0](.final("  Prueba de dictado  "))
        XCTAssertEqual(f.sink.values.count, 1)
        XCTAssertEqual(f.sink.values[0].contextID, "origin-session")
        XCTAssertEqual(f.sink.values[0].text, "Prueba de dictado")
        XCTAssertEqual(f.sink.values[0].completion, .recognizedFinal)
        XCTAssertTrue(f.sink.values[0].requiresReview)
        XCTAssertEqual(f.sink.values[0].source, "on_device_speech")
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertNil(f.controller.contextID)
        XCTAssertTrue(f.controller.partialText.isEmpty)
        XCTAssertEqual(f.driver.cancelCalls, 1)
    }

    @MainActor func testCancelledAndPreviousGenerationCallbacksCannotFillAnotherDraft() {
        let f = Fixture(); f.start("old")
        let old = f.driver.recognitionReceivers[0]
        old(.partial("privado anterior"))
        f.controller.cancel()
        old(.final("no publicar"))
        f.start("new")
        old(.partial("no mezclar"))
        old(.final("no mezclar final"))
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertTrue(f.controller.partialText.isEmpty)
        XCTAssertTrue(f.sink.values.isEmpty)
        f.driver.recognitionReceivers[1](.final("turno nuevo"))
        XCTAssertEqual(f.sink.values.map(\.contextID), ["new"])
        XCTAssertEqual(f.sink.values.map(\.text), ["turno nuevo"])
    }

    @MainActor func testCancelWhileAwaitingPermissionsPreventsDelayedCaptureAndAllowsRetry() {
        let f = Fixture()
        f.controller.startDictation(contextID: "old")
        f.controller.cancel()
        f.controller.startDictation(contextID: "new")
        f.driver.authorize(index: 0)
        XCTAssertEqual(f.controller.phase, .authorizing)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        f.driver.authorize(index: 1)
        XCTAssertEqual(f.driver.recognitionReceivers.count, 1)
        XCTAssertEqual(f.controller.contextID, "new")
        XCTAssertFalse(f.driver.permissionRequests[0].0.active)
    }

    @MainActor func testPermissionDeadlineDoesNotLeaveAQueuedCapture() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one")
        XCTAssertEqual(f.clock.entries[0].seconds, 30)
        f.clock.entries[0].fire()
        XCTAssertEqual(f.controller.issue, .authorizationTimeout)
        f.driver.authorize()
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
    }

    @MainActor func testCaptureLimitStopsInputThenWaitsForBoundedFinalization() {
        let f = Fixture(); f.start()
        f.driver.recognitionReceivers[0](.partial("texto parcial"))
        XCTAssertEqual(f.clock.entries.last?.seconds, 30)
        f.clock.entries.last?.fire()
        XCTAssertEqual(f.controller.phase, .finishing)
        XCTAssertEqual(f.driver.finishCalls, 1)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertEqual(f.clock.entries.last?.seconds, 3)
        f.driver.recognitionReceivers[0](.final("texto final"))
        XCTAssertEqual(f.sink.values.first?.completion, .recognizedFinal)
        XCTAssertEqual(f.sink.values.first?.text, "texto final")
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testFinalizationDeadlinePublishesPartialAsReviewRequiredExactlyOnce() {
        let f = Fixture(); f.start()
        f.driver.recognitionReceivers[0](.partial("texto a revisar"))
        f.controller.stopDictation()
        f.clock.entries.last?.fire()
        f.driver.recognitionReceivers[0](.final("callback tardío"))
        XCTAssertEqual(f.sink.values.count, 1)
        XCTAssertEqual(f.sink.values[0].completion, .partialAtStop)
        XCTAssertEqual(f.sink.values[0].text, "texto a revisar")
        XCTAssertTrue(f.sink.values[0].requiresReview)
        XCTAssertEqual(f.driver.cancelCalls, 1)
        XCTAssertEqual(f.controller.phase, .idle)
    }

    @MainActor func testEmptyFinalAndRecognitionErrorDoNotSendOrPublishAnything() {
        let f = Fixture(); f.start()
        f.driver.recognitionReceivers[0](.final("  \n"))
        XCTAssertTrue(f.sink.values.isEmpty)
        f.start()
        f.driver.recognitionReceivers[1](.partial("parcial"))
        f.driver.recognitionReceivers[1](.failed)
        XCTAssertEqual(f.controller.issue, .recognitionFailed)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertTrue(f.controller.partialText.isEmpty)
    }

    @MainActor func testUTF8BudgetStopsCaptureWithoutPublishingOversizedPartial() {
        let f = Fixture(maxBytes: 8); f.start()
        f.driver.recognitionReceivers[0](.partial("🟢🟢"))
        XCTAssertEqual(f.controller.partialText.utf8.count, 8)
        f.driver.recognitionReceivers[0](.partial("🟢🟢🟢"))
        XCTAssertEqual(f.controller.issue, .textLimit)
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertEqual(f.driver.cancelCalls, 1)
    }

    @MainActor func testCaptureSetupFailureIsSanitizedAndReleasesResources() {
        let f = Fixture()
        f.driver.beginRecognitionError = NSError(domain: "PRIVATE-DIAGNOSTIC", code: 1)
        f.start()
        XCTAssertEqual(f.controller.issue, .captureFailed)
        XCTAssertFalse(f.controller.statusText.contains("PRIVATE"))
        XCTAssertEqual(f.driver.cancelCalls, 1)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testSecondStartCannotCreateParallelMicrophoneOwnership() {
        let f = Fixture(); f.start("one")
        f.controller.startDictation(contextID: "two")
        XCTAssertEqual(f.controller.issue, .busy)
        XCTAssertEqual(f.driver.recognitionReceivers.count, 1)
        XCTAssertEqual(f.controller.contextID, "one")
        f.controller.cancel()
    }

    @MainActor func testLateCancelledDeadlineCannotStopNewCapture() {
        let f = Fixture(); f.start("old")
        let old = f.clock.entries.last!
        f.controller.cancel(); f.start("new")
        old.fire(evenIfCancelled: true)
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertEqual(f.controller.contextID, "new")
        XCTAssertEqual(f.driver.finishCalls, 0)
    }

    @MainActor func testMissingLocalVoiceAndUnknownSelectionNeverFallBack() {
        let f = Fixture(); f.driver.voices = []
        f.controller.play(text: "prueba", contextID: "one")
        XCTAssertEqual(f.controller.issue, .noLocalVoice)
        XCTAssertTrue(f.driver.playbackTexts.isEmpty)
        f.driver.voices = [.init(id: "com.apple.voice.fixture", name: "Local", language: "es-ES")]
        f.controller.play(text: "prueba", contextID: "one", voiceID: "com.external.cloud")
        XCTAssertEqual(f.controller.issue, .noLocalVoice)
        XCTAssertTrue(f.driver.playbackTexts.isEmpty)
    }

    @MainActor func testPlaybackUsesChosenLocalVoiceAndReleasesAfterFinish() {
        let f = Fixture()
        f.controller.play(text: "Respuesta sintética", contextID: "one")
        XCTAssertEqual(f.controller.phase, .speaking)
        XCTAssertEqual(f.driver.playbackTexts, ["Respuesta sintética"])
        XCTAssertEqual(f.driver.selectedVoices.first?.id, "com.apple.voice.fixture")
        XCTAssertEqual(f.clock.entries.last?.seconds, 120)
        f.driver.playbackReceivers[0](.finished)
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertNil(f.controller.activeVoiceName)
        XCTAssertEqual(f.clock.activeCount, 0)
        XCTAssertTrue(f.sink.values.isEmpty)
    }

    @MainActor func testOldPlaybackCompletionCannotStopAReplacement() {
        let f = Fixture()
        f.controller.play(text: "Anterior", contextID: "old")
        let old = f.driver.playbackReceivers[0]
        f.controller.play(text: "Actual", contextID: "new")
        old(.cancelled)
        XCTAssertEqual(f.controller.phase, .speaking)
        XCTAssertEqual(f.controller.contextID, "new")
        f.driver.playbackReceivers[1](.finished)
        XCTAssertEqual(f.controller.phase, .idle)
    }

    @MainActor func testStartingDictationStopsPlaybackWithoutAStaleSpeakerCallback() {
        let f = Fixture()
        f.controller.play(text: "Prueba", contextID: "one")
        let old = f.driver.playbackReceivers[0]
        f.controller.startDictation(contextID: "two")
        old(.failed)
        XCTAssertEqual(f.controller.phase, .authorizing)
        XCTAssertNil(f.controller.issue)
        f.driver.authorize()
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertEqual(f.controller.contextID, "two")
        XCTAssertGreaterThanOrEqual(f.driver.stopPlaybackCalls, 2)
    }

    @MainActor func testPlaybackWhileCapturingIsRejectedWithoutChangingTheInput() {
        let f = Fixture(); f.start("one")
        f.controller.play(text: "No reproducir", contextID: "one")
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertEqual(f.controller.issue, .busy)
        XCTAssertTrue(f.driver.playbackTexts.isEmpty)
        XCTAssertEqual(f.driver.finishCalls, 0)
    }

    @MainActor func testPlaybackBudgetAndSetupFailureReleaseOutput() {
        let f = Fixture(maxBytes: 8)
        f.controller.play(text: "🟢🟢🟢", contextID: "one")
        XCTAssertEqual(f.controller.issue, .textLimit)
        XCTAssertTrue(f.driver.voiceQueries.isEmpty)
        f.driver.beginPlaybackError = NSError(domain: "PRIVATE", code: 1)
        f.controller.play(text: "prueba", contextID: "one")
        XCTAssertEqual(f.controller.issue, .playbackFailed)
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertFalse(f.controller.statusText.contains("PRIVATE"))
    }

    @MainActor func testPlaybackTimeoutStopsAndDoesNotClaimFullCompletion() {
        let f = Fixture()
        f.controller.play(text: "Respuesta larga", contextID: "one")
        f.clock.entries.last?.fire()
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertEqual(f.controller.issue, .playbackTimeout)
        XCTAssertNil(f.controller.activeVoiceName)
        f.driver.playbackReceivers[0](.finished)
        XCTAssertEqual(f.controller.issue, .playbackTimeout)
    }

    @MainActor func testInvalidContextNeverRequestsAudioOrVoiceInventory() {
        let f = Fixture()
        f.controller.startDictation(contextID: "")
        f.controller.play(text: "prueba", contextID: "\0")
        XCTAssertEqual(f.controller.issue, .invalidContext)
        XCTAssertTrue(f.driver.permissionRequests.isEmpty)
        XCTAssertTrue(f.driver.voiceQueries.isEmpty)
    }

    @MainActor func testInlinePermissionCallbacksDoNotLeaveAuthorizationOwnership() {
        let f = Fixture()
        f.driver.inlinePermissions = .init(speech: .authorized, microphone: .authorized)
        f.controller.startDictation(contextID: "one", localeIdentifier: "es-MX")
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertEqual(f.driver.recognitionLocales, ["es-MX"])
        XCTAssertFalse(f.driver.permissionRequests[0].0.active)
        XCTAssertEqual(f.clock.activeCount, 1)
        f.controller.cancel()
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testMissingBundlePermissionKeysFailWithoutStartingRecognition() {
        let f = Fixture()
        f.driver.inlinePermissions = .init(speech: .authorized, microphone: .authorized, configurationMissing: true)
        f.controller.startDictation(contextID: "one")
        XCTAssertEqual(f.controller.issue, .configuration)
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertTrue(f.driver.recognitionReceivers.isEmpty)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testSynchronousFinalOrPlaybackCompletionLeavesNoDeadline() {
        let f = Fixture()
        f.driver.inlineRecognition = .final("Prueba inmediata")
        f.start()
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertEqual(f.sink.values.count, 1)
        XCTAssertEqual(f.clock.activeCount, 0)
        f.driver.inlinePlayback = .finished
        f.controller.play(text: "Respuesta", contextID: "one")
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    @MainActor func testDeinitializationRetainsAdapterUntilCancellationRuns() async {
        let driver = FakeDriver(), clock = FakeClock()
        var controller: LocalVoiceController? = LocalVoiceController(driver: driver, clock: clock)
        weak var observed = controller
        controller?.startDictation(contextID: "one")
        controller = nil
        for _ in 0..<10 where driver.cancelCalls == 0 { await Task.yield() }
        XCTAssertNil(observed)
        XCTAssertEqual(driver.cancelCalls, 1)
        XCTAssertFalse(driver.permissionRequests[0].0.active)
        XCTAssertEqual(clock.activeCount, 0)
        driver.authorize()
        XCTAssertTrue(driver.recognitionReceivers.isEmpty)
        observed = nil
    }

    @MainActor func testSecondStopCancelsFinalizationAndDiscardsPendingDraftMaterial() {
        let f = Fixture(); f.start("one")
        f.driver.recognitionReceivers[0](.partial("parcial descartado"))
        f.controller.stopDictation()
        XCTAssertEqual(f.controller.phase, .finishing)
        let deadline = f.clock.entries.last!
        f.controller.stopDictation()
        deadline.fire(evenIfCancelled: true)
        f.driver.recognitionReceivers[0](.final("final tardío"))
        XCTAssertEqual(f.controller.phase, .idle)
        XCTAssertTrue(f.sink.values.isEmpty)
        XCTAssertEqual(f.clock.activeCount, 0)
    }

    func testAppleLocalVoicePolicyRejectsCloudAndPersonalProviders() {
        XCTAssertTrue(MacLocalVoicePolicy.isAllowed(identifier: "com.apple.voice.compact.es-ES.Monica", isPersonalVoice: false))
        XCTAssertTrue(MacLocalVoicePolicy.isAllowed(identifier: "com.apple.ttsbundle.fixture", isPersonalVoice: false))
        for value in ["com.external.cloud.voice", "com.apple.voiceEvil.remote", "com.apple.voice.fixture\0cloud",
                      "com.apple.voice.fixture\n", "", String(repeating: "x", count: 257)] {
            XCTAssertFalse(MacLocalVoicePolicy.isAllowed(identifier: value, isPersonalVoice: false))
        }
        XCTAssertFalse(MacLocalVoicePolicy.isAllowed(identifier: "com.apple.voice.fixture", isPersonalVoice: true))
    }

    @MainActor func testChangingPreferencesDoesNotCaptureAndExtendedDictationHasFiniteBudget() {
        let f = Fixture()
        f.controller.preferences.mode = .extendedDictation
        f.controller.preferences.localeIdentifier = "es-MX"
        XCTAssertTrue(f.driver.permissionRequests.isEmpty)
        XCTAssertTrue(f.driver.voiceQueries.isEmpty)
        f.controller.startSelectedDictation(contextID: "one"); f.driver.authorize()
        XCTAssertEqual(f.driver.recognitionLocales, ["es-MX"])
        XCTAssertEqual(f.clock.entries.last?.seconds, 120)
        XCTAssertEqual(f.controller.activeCaptureMode, .extendedDictation)
        f.controller.cancel()
    }

    @MainActor func testWakePhraseNeverPublishesPreTriggerAudioAndOnlyOffersSuffixForReview() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one", mode: .wakePhrase, wakePhrase: "Hola Jarvis")
        f.driver.authorize()
        XCTAssertEqual(f.clock.entries.last?.seconds, 300)
        f.driver.recognitionReceivers[0](.partial("audio anterior que no debe llegar al borrador"))
        XCTAssertTrue(f.controller.partialText.isEmpty); XCTAssertFalse(f.controller.wakePhraseDetected)
        f.driver.recognitionReceivers[0](.partial("audio anterior. Hola Jarvis, prepara un borrador"))
        XCTAssertTrue(f.controller.wakePhraseDetected)
        XCTAssertEqual(f.controller.partialText, "prepara un borrador")
        f.driver.recognitionReceivers[0](.final("audio anterior. Hola Jarvis, prepara un borrador revisado."))
        XCTAssertEqual(f.sink.values.map(\.text), ["prepara un borrador revisado."])
        XCTAssertTrue(f.sink.values[0].requiresReview)
        XCTAssertNotNil(f.sink.values[0].captureID)
        XCTAssertEqual(f.controller.phase, .idle)
    }

    @MainActor func testWakePhraseWholeWordAndWithdrawnHypothesisCannotPublishAnUntriggeredDraft() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one", mode: .wakePhrase, wakePhrase: "Jarvis"); f.driver.authorize()
        f.driver.recognitionReceivers[0](.partial("Jarvisito no es la frase"))
        XCTAssertFalse(f.controller.wakePhraseDetected)
        f.driver.recognitionReceivers[0](.partial("Jarvis, texto provisional"))
        f.driver.recognitionReceivers[0](.final("La hipótesis corregida ya no tiene activación"))
        XCTAssertEqual(f.controller.issue, .recognitionFailed)
        XCTAssertTrue(f.sink.values.isEmpty)
        f.controller.startDictation(contextID: "two", mode: .wakePhrase, wakePhrase: "Jarvis"); f.driver.authorize()
        f.driver.recognitionReceivers[1](.partial("sin activación"))
        f.clock.entries.last?.fire(); f.clock.entries.last?.fire()
        XCTAssertEqual(f.controller.issue, .wakePhraseNotHeard)
        XCTAssertTrue(f.sink.values.isEmpty)
    }

    @MainActor func testInvalidWakePhraseOrLocaleNeverRequestsPermission() {
        let f = Fixture()
        for phrase in ["", "uno dos tres cuatro cinco", "Jarvis\n", String(repeating: "x", count: 65)] {
            f.controller.startDictation(contextID: "one", mode: .wakePhrase, wakePhrase: phrase)
            XCTAssertEqual(f.controller.issue, .invalidSettings)
        }
        f.controller.startDictation(contextID: "one", localeIdentifier: "es/ES")
        XCTAssertTrue(f.driver.permissionRequests.isEmpty)
    }

    @MainActor func testInputChangeRevocationAndInterruptionDiscardUnconfirmedPartial() {
        for issue in [LocalVoiceIssue.microphoneDenied, .speechDenied, .interrupted, .inputChanged] {
            let f = Fixture(); var receipts: [LocalVoiceCaptureReceipt] = []
            f.controller.onCaptureCompletion = { receipts.append($0) }
            f.start("origin"); let captureID = f.controller.captureIdentifier
            f.driver.recognitionReceivers[0](.partial("no conservar sin confirmar"))
            f.driver.recognitionReceivers[0](.issue(issue))
            XCTAssertEqual(f.controller.phase, .idle); XCTAssertEqual(f.controller.issue, issue)
            XCTAssertTrue(f.sink.values.isEmpty); XCTAssertEqual(f.clock.activeCount, 0)
            XCTAssertEqual(receipts, [.init(contextID: "origin", captureID: captureID, outcome: .failed, issue: issue)])
            f.driver.recognitionReceivers[0](.final("tardío")); XCTAssertTrue(f.sink.values.isEmpty)
        }
    }

    @MainActor func testPlaybackReceiptsSeparateFinishedCancelledAndDeadlineWithExactIdentity() {
        let f = Fixture(); var receipts: [LocalVoicePlaybackReceipt] = []
        f.controller.onPlaybackCompletion = { receipts.append($0) }
        let first = UUID(), second = UUID()
        f.controller.play(text: "Primero", contextID: "one", playbackID: first)
        f.driver.playbackReceivers[0](.finished)
        XCTAssertEqual(receipts, [.init(contextID: "one", playbackID: first, event: .finished)])
        f.controller.play(text: "Segundo", contextID: "one", playbackID: second)
        f.clock.entries.last?.fire()
        XCTAssertEqual(receipts.last, .init(contextID: "one", playbackID: second, event: .failed, issue: .playbackTimeout))
        f.driver.playbackReceivers[0](.cancelled); XCTAssertEqual(receipts.count, 2)
    }

    @MainActor func testAdapterCannotReturnCloudVoiceWrongLanguageOrDuplicateChoices() {
        let f = Fixture()
        f.driver.voices = [.init(id: "com.external.cloud", name: "Cloud", language: "es-ES"),
                           .init(id: "com.apple.voice.wrong", name: "Otro idioma", language: "en-US")]
        f.controller.playSelected(text: "No usar voces no elegibles", contextID: "one")
        XCTAssertEqual(f.controller.issue, .noLocalVoice); XCTAssertTrue(f.driver.playbackTexts.isEmpty)
        let allowed = LocalVoiceChoice(id: "com.apple.voice.local", name: "Local", language: "es-ES")
        f.driver.voices = [allowed, allowed]
        XCTAssertEqual(f.controller.availableVoices(localeIdentifier: "es-ES"), [allowed])
    }

    @MainActor func testTalkSilenceEndpointFinishesInputButWaitsForFinalAndOldSilenceCannotInterruptNewWords() {
        let f = Fixture()
        f.controller.startDictation(contextID: "one", endpointAfterSilence: 1.5); f.driver.authorize()
        f.driver.recognitionReceivers[0](.partial("Primera palabra"))
        let previousSilence = f.clock.entries.last!
        f.driver.recognitionReceivers[0](.partial("Primera palabra y continuación"))
        previousSilence.fire(evenIfCancelled: true)
        XCTAssertEqual(f.controller.phase, .listening); XCTAssertEqual(f.driver.finishCalls, 0)
        XCTAssertEqual(f.clock.entries.last?.seconds, 1.5)
        f.clock.entries.last?.fire()
        XCTAssertEqual(f.controller.phase, .finishing); XCTAssertEqual(f.driver.finishCalls, 1)
        XCTAssertTrue(f.sink.values.isEmpty, "Silence alone never authorizes a partial transcript")
        f.driver.recognitionReceivers[0](.final("Primera palabra y continuación."))
        XCTAssertEqual(f.sink.values.first?.completion, .recognizedFinal)
    }

    @MainActor func testOrdinaryDictationHasNoSilenceEndpointOrImplicitSend() {
        let f = Fixture(); f.start("one")
        f.driver.recognitionReceivers[0](.partial("Borrador que sigo editando"))
        XCTAssertEqual(f.controller.phase, .listening)
        XCTAssertFalse(f.clock.entries.contains { $0.seconds == 1.5 })
        XCTAssertTrue(f.sink.values.isEmpty)
        f.controller.cancel()
    }
}
