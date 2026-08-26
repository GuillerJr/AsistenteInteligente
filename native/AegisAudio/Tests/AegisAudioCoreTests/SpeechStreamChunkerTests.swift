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
}
