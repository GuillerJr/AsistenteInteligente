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
    for index in 0..<600 {
        let job = UUID()
        transcript.begin("Pregunta \(index)")
        transcript.attach(job)
        transcript.finish("Respuesta \(index)", jobID: job)
    }
    #expect(transcript.messages.count == 1_000)
    #expect(transcript.messages.first?.text == "Pregunta 100")
    #expect(transcript.messages.last?.text == "Respuesta 599")
}

@Test func textChatHistoricalWindowCanReachMessagesOlderThanItsVisibleLimit() {
    let history = (0..<2_000).map { TextChatTranscript.Message(role: .user, text: "Mensaje \($0)") }
    var transcript = TextChatTranscript()
    transcript.restore(Array(history.suffix(1_000)))
    for first in stride(from: 980, through: 0, by: -20) {
        transcript.prepend(Array(history[first..<(first + 20)]))
        #expect(transcript.messages.count == TextChatTranscript.maximumVisibleMessages)
        #expect(transcript.messages.first?.id == history[first].id)
        #expect(transcript.messages.last?.id == history[first + 999].id)
        #expect(Set(transcript.messages.map(\.id)).count == transcript.messages.count)
        #expect(transcript.hasNewerHistory)
    }
    let historical = transcript.messages
    let rejectedHistoricalSend = transcript.begin("Una instrucción nueva")
    #expect(!rejectedHistoricalSend)
    #expect(transcript.messages == historical)
    transcript.restore(Array(history.suffix(20)))
    #expect(!transcript.hasNewerHistory)
    let acceptedRecentSend = transcript.begin("Una instrucción nueva")
    #expect(acceptedRecentSend)
    #expect(transcript.messages.suffix(2).first?.text == "Una instrucción nueva")
    #expect(transcript.messages.count == 22)
}

@Test func textChatHistoricalPagingDeduplicatesAndCannotReplaceAnActiveTurn() {
    let history = (0..<1_001).map { TextChatTranscript.Message(role: .assistant, text: "Resultado \($0)") }
    var transcript = TextChatTranscript()
    transcript.restore(Array(history.suffix(1_000)))
    transcript.prepend(Array(history.suffix(20)))
    #expect(!transcript.hasNewerHistory)
    transcript.prepend([history[0]])
    #expect(transcript.messages.first?.id == history[0].id)
    #expect(transcript.hasNewerHistory)
    transcript.restore(Array(history.suffix(20)))
    transcript.begin("Pregunta")
    let job = UUID()
    transcript.attach(job)
    let active = transcript.messages
    transcript.prepend([history[0]])
    transcript.restore(history)
    #expect(transcript.messages == active)
    #expect(transcript.jobID == job)
    #expect(!transcript.hasNewerHistory)
}

@Test func terminalCommandQuotesExecutableAndEnvironmentAsLiteralArguments() {
    let command = JarvisTerminalCommand.script(executable: "/Applications/Jarvis '$(touch evil)'.app/cli",
                                               environment: ["HOME": "/Users/a b", "AEGIS_WORKSPACE_ROOT": "/tmp/`evil`"])
    #expect(command.contains("'/Applications/Jarvis '\\''$(touch evil)'\\''.app/cli' --cli engineer"))
    #expect(command.contains("'HOME=/Users/a b'"))
    #expect(command.contains("'AEGIS_WORKSPACE_ROOT=/tmp/`evil`'"))
    #expect(command.contains("/usr/bin/env -i"))
}
