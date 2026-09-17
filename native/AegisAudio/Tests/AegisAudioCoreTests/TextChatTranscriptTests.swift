import Foundation
import Testing
@testable import AegisAudioCore

@Test func textChatRejectsRewrittenStreamsAndLateCancelledJobUpdates() {
    var transcript = TextChatTranscript()
    let job = UUID()
    transcript.begin("Pregunta")
    transcript.attach(job)
    let initial = transcript.update("Una respuesta", jobID: job)
    let rewritten = transcript.update("Otra respuesta", jobID: job)
    let oversized = transcript.update(String(repeating: "á", count: 20_000), jobID: job)
    #expect(initial)
    #expect(!rewritten)
    #expect(!oversized)
    transcript.finish("Respuesta cancelada.", jobID: job, interrupted: true)
    #expect(transcript.messages.last?.text == "Una respuesta\n\nRespuesta cancelada.")
    let late = transcript.update("Una respuesta tardía", jobID: job)
    #expect(!late)
    transcript.begin("Nueva pregunta")
    transcript.attach(UUID())
    transcript.finish("Resultado antiguo", jobID: job)
    #expect(transcript.messages.last?.text == "")
}

@Test func textChatBoundsVisibleHistoryWithoutLosingTheActiveMessage() {
    var transcript = TextChatTranscript()
    for index in 0..<60 {
        let job = UUID()
        transcript.begin("Pregunta \(index)")
        transcript.attach(job)
        transcript.finish("Respuesta \(index)", jobID: job)
    }
    #expect(transcript.messages.count == 100)
    #expect(transcript.messages.first?.text == "Pregunta 10")
    #expect(transcript.messages.last?.text == "Respuesta 59")
}

@Test func terminalCommandQuotesExecutableAndEnvironmentAsLiteralArguments() {
    let command = JarvisTerminalCommand.script(executable: "/Applications/Jarvis '$(touch evil)'.app/cli",
                                               environment: ["HOME": "/Users/a b", "AEGIS_WORKSPACE_ROOT": "/tmp/`evil`"])
    #expect(command.contains("'/Applications/Jarvis '\\''$(touch evil)'\\''.app/cli' --cli engineer"))
    #expect(command.contains("'HOME=/Users/a b'"))
    #expect(command.contains("'AEGIS_WORKSPACE_ROOT=/tmp/`evil`'"))
    #expect(command.contains("/usr/bin/env -i"))
}
