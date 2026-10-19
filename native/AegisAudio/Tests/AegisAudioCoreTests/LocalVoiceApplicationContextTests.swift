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

    @Test("Retains Unicode names while rejecting controls and excessive UTF-8")
    func applicationNameBoundaries() {
        let accepted = [
            ("Finder", "Finder"),
            ("  A\u{00A0}  B  ", "A B"),
            (String(repeating: "a", count: 160), String(repeating: "a", count: 160)),
            (String(repeating: "🧠", count: 40), String(repeating: "🧠", count: 40)),
        ]
        for (input, expected) in accepted {
            let result = LocalVoiceApplicationContextCommand.sanitizedApplicationName(input)
            #expect(result == expected)
        }
        for input in ["App\u{0000}", "App\u{007F}", "App\u{0085}", "App\u{200E}",
                      String(repeating: "🧠", count: 41), "   "] {
            #expect(LocalVoiceApplicationContextCommand.sanitizedApplicationName(input) == nil)
        }
    }
}
