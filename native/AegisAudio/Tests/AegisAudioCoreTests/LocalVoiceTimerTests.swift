import Testing
@testable import AegisAudioCore

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

    @Test("Allows only one active timer and cancels it explicitly")
    @MainActor
    func schedulerLifecycle() {
        let scheduler = LocalVoiceTimerScheduler()

        #expect(scheduler.start(durationSeconds: 60) {})
        #expect(scheduler.isActive)
        #expect(!scheduler.start(durationSeconds: 1) {})
        #expect(scheduler.cancel())
        #expect(!scheduler.isActive)
        #expect(!scheduler.cancel())
    }
}
