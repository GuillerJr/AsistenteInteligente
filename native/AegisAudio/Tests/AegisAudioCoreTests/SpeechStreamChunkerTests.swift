import Testing
@testable import AegisAudioCore

@Suite("Speech stream chunker")
struct SpeechStreamChunkerTests {
    @Test("Emits only complete sentences and never repeats them")
    func sentenceStreaming() {
        var chunker = SpeechStreamChunker()

        #expect(chunker.consume("Hola.") == [])
        #expect(chunker.consume("Hola. Estoy") == ["Hola."])
        #expect(chunker.consume("Hola. Estoy listo. Ahora") == ["Estoy listo."])
        #expect(chunker.finish("Hola. Estoy listo. Ahora sí") == ["Ahora sí"])
    }

    @Test("Rejects a non-monotonic provider snapshot")
    func nonMonotonicSnapshot() {
        var chunker = SpeechStreamChunker()
        #expect(chunker.consume("Primera frase. Segunda") == ["Primera frase."])
        #expect(chunker.consume("Texto reemplazado") == [])
        #expect(chunker.finish("Texto reemplazado") == ["Texto reemplazado"])
    }

    @Test("Emits a complete long clause before sentence punctuation")
    func clauseStreaming() {
        var chunker = SpeechStreamChunker()
        let snapshot = "Voy a revisar primero el calendario y los recordatorios, después continúo"

        #expect(
            chunker.consume(snapshot)
                == ["Voy a revisar primero el calendario y los recordatorios,"]
        )
        #expect(chunker.finish(snapshot) == ["después continúo"])
    }

    @Test("Bounds an unpunctuated streaming buffer")
    func boundedStreaming() {
        var chunker = SpeechStreamChunker()
        let snapshot = Array(repeating: "palabra", count: 24).joined(separator: " ")

        let streamed = chunker.consume(snapshot)
        let remainder = chunker.finish(snapshot)

        #expect(streamed.count == 1)
        #expect(streamed[0].count <= 160)
        #expect((streamed + remainder).joined(separator: " ") == snapshot)
    }
}
