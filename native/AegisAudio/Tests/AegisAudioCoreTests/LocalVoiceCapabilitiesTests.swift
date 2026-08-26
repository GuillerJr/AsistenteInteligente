import Testing
@testable import AegisAudioCore

@Suite("Local voice capabilities")
struct LocalVoiceCapabilitiesTests {
    @Test("Parses only exact capability questions")
    func commands() {
        for transcript in [
            "¿Qué puedes hacer?",
            "Jarvis, ¿qué sabes hacer?",
            "¿Cómo puedes ayudarme?",
            "What can you do?",
            "Jarvis, how can you help me?",
        ] {
            #expect(
                LocalVoiceCapabilitiesCommand.parse(transcript) == .describeCapabilities
            )
        }
        for transcript in [
            "Qué puedes hacer y abre Safari",
            "Ayúdame a enviar un correo",
            "Qué puede hacer esta aplicación",
            "Dime qué sabes hacer con mi contraseña",
        ] {
            #expect(LocalVoiceCapabilitiesCommand.parse(transcript) == nil)
        }
    }

    @Test("Shared normalization keeps exact commands strict")
    func sharedNormalization() {
        #expect(
            LocalVoiceApplicationContextCommand.parse(
                "Jarvis, ¿qué aplicación estoy usando?"
            ) == .activeApplication
        )
        #expect(
            LocalVoiceTimerCommand.parse("Jarvis, ¿estado del temporizador?") == .status
        )
        #expect(
            LocalVoiceCommandText.normalize("  JARVIS, ¿Qué   puedes hacer?  ")
                == "que puedes hacer"
        )
    }

    @Test("Capability response reflects visual control readiness")
    func response() {
        let ready = LocalVoiceCapabilitiesCommand.spokenResponse(visualControlReady: true)
        let pending = LocalVoiceCapabilitiesCommand.spokenResponse(visualControlReady: false)

        #expect(ready.contains("controlar visualmente una app"))
        #expect(!ready.contains("cuando actives Pantalla y Control"))
        #expect(!pending.contains("controlar visualmente una app"))
        #expect(pending.contains("cuando actives Pantalla y Control"))
    }
}
