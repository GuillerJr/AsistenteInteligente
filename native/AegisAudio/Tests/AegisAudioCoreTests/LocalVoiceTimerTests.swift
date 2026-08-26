import Testing
@testable import AegisAudioCore

@MainActor
private final class TimerCompletionFlag {
    var completed = false
}

@Suite("Local voice timer")
struct LocalVoiceTimerTests {
    @Test("Parses bounded start commands")
    func startCommands() {
        let commands = [
            ("Pon un temporizador de 5 minutos", 300),
            ("Jarvis, configura un temporizador por 30 segundos", 30),
            ("Start a timer for 2 hours", 7_200),
            ("Timer for 1 minute", 60),
        ]
        for (transcript, seconds) in commands {
            #expect(
                LocalVoiceTimerCommand.parse(transcript) == .start(durationSeconds: seconds)
            )
        }
    }

    @Test("Parses cancel commands")
    func cancelCommands() {
        for transcript in [
            "Cancela el temporizador",
            "Jarvis, detén el temporizador",
            "Cancel timer",
        ] {
            #expect(LocalVoiceTimerCommand.parse(transcript) == .cancel)
        }
    }

    @Test("Parses status commands")
    func statusCommands() {
        for transcript in [
            "Cuánto falta del temporizador",
            "Jarvis, cuánto tiempo queda del temporizador",
            "Estado del temporizador",
            "Timer status",
        ] {
            #expect(LocalVoiceTimerCommand.parse(transcript) == .status)
        }
    }

    @Test("Parses pause and resume commands")
    func pauseAndResumeCommands() {
        for transcript in ["Pausa el temporizador", "Jarvis, pause timer"] {
            #expect(LocalVoiceTimerCommand.parse(transcript) == .pause)
        }
        for transcript in [
            "Reanuda el temporizador",
            "Jarvis, continúa el temporizador",
            "Resume timer",
        ] {
            #expect(LocalVoiceTimerCommand.parse(transcript) == .resume)
        }
    }

    @Test("Rejects ambiguous or unbounded commands")
    func invalidCommands() {
        for transcript in [
            "Pon un temporizador",
            "Pon un temporizador de cero minutos",
            "Pon un temporizador de 0 minutos",
            "Pon un temporizador de 25 horas",
            "Pon un temporizador de 1,5 minutos",
            "Recuérdame algo en 5 minutos",
        ] {
            #expect(LocalVoiceTimerCommand.parse(transcript) == nil)
        }
    }

    @Test("Pauses, resumes and cancels one timer explicitly")
    @MainActor
    func schedulerLifecycle() {
        let scheduler = LocalVoiceTimerScheduler()

        #expect(scheduler.remainingSeconds == nil)
        #expect(scheduler.start(durationSeconds: 60) {})
        #expect(scheduler.isActive)
        #expect(!scheduler.isPaused)
        #expect((59 ... 60).contains(scheduler.remainingSeconds ?? 0))
        #expect(!scheduler.start(durationSeconds: 1) {})
        let paused = scheduler.pause()
        #expect((59 ... 60).contains(paused ?? 0))
        #expect(scheduler.isActive)
        #expect(scheduler.isPaused)
        #expect(scheduler.remainingSeconds == paused)
        #expect(scheduler.pause() == nil)
        #expect(!scheduler.start(durationSeconds: 1) {})
        #expect(scheduler.resume() == paused)
        #expect(scheduler.isActive)
        #expect(!scheduler.isPaused)
        #expect(scheduler.resume() == nil)
        #expect(scheduler.cancel())
        #expect(!scheduler.isActive)
        #expect(!scheduler.isPaused)
        #expect(scheduler.remainingSeconds == nil)
        #expect(!scheduler.cancel())
    }

    @Test("Preserves completion across pause and resume")
    @MainActor
    func resumedTimerCompletes() async throws {
        let scheduler = LocalVoiceTimerScheduler()
        let flag = TimerCompletionFlag()

        #expect(scheduler.start(durationSeconds: 1) { flag.completed = true })
        #expect(scheduler.pause() == 1)
        #expect(scheduler.resume() == 1)
        try await Task.sleep(for: .milliseconds(1_100))

        #expect(flag.completed)
        #expect(!scheduler.isActive)
        #expect(scheduler.remainingSeconds == nil)
    }
}
