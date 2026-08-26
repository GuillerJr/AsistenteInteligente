import Testing
@testable import AegisAudioCore

@Suite("Local voice application context")
struct LocalVoiceApplicationContextTests {
    @Test("Parses only exact active application questions")
    func commands() {
        for transcript in [
            "¿Qué aplicación estoy usando?",
            "Jarvis, cuál es la aplicación activa",
            "Qué app estoy usando",
            "What app am I using",
        ] {
            #expect(
                LocalVoiceApplicationContextCommand.parse(transcript) == .activeApplication
            )
        }
        for transcript in [
            "Qué aplicación estoy usando y qué hora es",
            "Abre la aplicación activa",
            "Qué aplicación usa más memoria",
        ] {
            #expect(LocalVoiceApplicationContextCommand.parse(transcript) == nil)
        }
    }

    @Test("Bounds and sanitizes the local application name")
    func applicationName() {
        #expect(
            LocalVoiceApplicationContextCommand.sanitizedApplicationName("  Visual   Studio Code  ")
                == "Visual Studio Code"
        )
        #expect(LocalVoiceApplicationContextCommand.sanitizedApplicationName(nil) == nil)
        #expect(LocalVoiceApplicationContextCommand.sanitizedApplicationName(" \t ") == nil)
        #expect(LocalVoiceApplicationContextCommand.sanitizedApplicationName("Unsafe\nApp") == nil)
        #expect(
            LocalVoiceApplicationContextCommand.sanitizedApplicationName(
                String(repeating: "a", count: 161)
            ) == nil
        )
    }
}
