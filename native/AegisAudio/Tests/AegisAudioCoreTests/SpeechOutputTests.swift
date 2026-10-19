import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Single voice output", .serialized)
@MainActor
struct SpeechOutputTests {
    @Test("Missing IPC never selects another voice and completes once")
    func noAlternateVoice() {
        let output = SpeechOutput()
        var outcomes: [SpeechPlaybackOutcome] = []
        output.speak("Hola", ipcSecret: nil) { outcomes.append($0) }
        output.finishStream()
        #expect(outcomes == [.unavailable])
        #expect(!output.isActive)
        #expect(!output.hasAudiblePlayback)
    }

    @Test("Failed streaming waits for final text and cannot restart itself")
    func failureIsSticky() {
        let output = SpeechOutput()
        var outcomes: [SpeechPlaybackOutcome] = []
        output.beginStream(ipcSecret: nil) { outcomes.append($0) }
        output.enqueue("Primera frase.")
        output.enqueue("Segunda frase.")
        #expect(outcomes.isEmpty)
        output.finishStream()
        output.enqueue("Texto tardío.")
        #expect(outcomes == [.unavailable])
        #expect(!output.isActive)
    }

    @Test("Stopping suppresses the old completion")
    func stopInvalidatesCompletion() async {
        let output = SpeechOutput()
        var outcomes: [SpeechPlaybackOutcome] = []
        output.beginStream(ipcSecret: nil) { outcomes.append($0) }
        output.enqueue("Respuesta anterior")
        await output.interruptWithFade()
        output.finishStream()
        #expect(outcomes.isEmpty)
        #expect(!output.isActive)
        output.speak("Nueva respuesta", ipcSecret: nil) { outcomes.append($0) }
        #expect(outcomes == [.unavailable])
    }

    @Test("An empty or oversized answer is never reported as spoken", arguments: [false, true])
    func invalidText(oversized: Bool) {
        let output = SpeechOutput()
        var outcome: SpeechPlaybackOutcome?
        let text = oversized ? String(repeating: "x", count: 2_001) : "   "
        output.speak(text, ipcSecret: nil) { outcome = $0 }
        #expect(outcome == .invalidText)
        #expect(!output.isActive)
    }

    @Test("An uncached private replay stays local and silent")
    func replayMiss() {
        let output = SpeechOutput()
        var outcome: SpeechPlaybackOutcome?
        output.replayLocally("Contenido privado") { outcome = $0 }
        #expect(outcome == .localReplayUnavailable)
        #expect(!output.isActive)
        #expect(!output.hasAudiblePlayback)
    }
}
