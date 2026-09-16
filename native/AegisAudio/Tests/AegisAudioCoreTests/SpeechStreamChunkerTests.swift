import Testing
@testable import AegisAudioCore

@Suite("Speech stream chunker")
struct SpeechStreamChunkerTests {
    @Test("Groups short sentences without repeating or losing text")
    func sentenceStreaming() {
        var chunker = SpeechStreamChunker()

        #expect(chunker.consume("Hola.") == [])
        #expect(chunker.consume("Hola. Estoy") == [])
        #expect(chunker.consume("Hola. Estoy listo. Ahora") == [])
        #expect(chunker.finish("Hola. Estoy listo. Ahora sí") == ["Hola. Estoy listo. Ahora sí"])
        #expect(chunker.finish("Hola. Estoy listo. Ahora sí") == [])
    }

    @Test("Rejects a non-monotonic provider snapshot")
    func nonMonotonicSnapshot() {
        var chunker = SpeechStreamChunker()
        #expect(chunker.consume("Primera frase. Segunda") == [])
        #expect(chunker.consume("Texto reemplazado") == [])
        #expect(chunker.isInvalid)
        #expect(chunker.finish("Texto reemplazado") == [])

        chunker.reset()
        #expect(!chunker.isInvalid)
        #expect(chunker.finish("Texto reemplazado") == ["Texto reemplazado"])
    }

    @Test("Preserves a thought across an ordinary comma")
    func clauseStreaming() {
        var chunker = SpeechStreamChunker()
        let snapshot = "Voy a revisar primero el calendario y los recordatorios, después continúo"

        #expect(chunker.consume(snapshot) == [])
        #expect(chunker.finish(snapshot) == [snapshot])
    }

    @Test("Bounds an unpunctuated streaming buffer")
    func boundedStreaming() {
        var chunker = SpeechStreamChunker()
        let snapshot = Array(repeating: "palabra", count: 48).joined(separator: " ")

        let streamed = chunker.consume(snapshot)
        let remainder = chunker.finish(snapshot)

        #expect(streamed.count == 1)
        #expect(streamed[0].count <= 320)
        #expect((streamed + remainder).joined(separator: " ") == snapshot)
    }

    @Test("Bounds the complete spoken stream")
    func completeStreamIsBounded() {
        var chunker = SpeechStreamChunker()
        let snapshot = Array(repeating: "respuesta", count: 400).joined(separator: " ")

        let spoken = chunker.consume(snapshot) + chunker.finish(snapshot)
        let combined = spoken.joined(separator: " ")

        #expect(combined.count <= 2_000)
        #expect(snapshot.hasPrefix(combined))
        #expect(!chunker.isInvalid)
    }

    @Test("Streams substantial sentences before the final response")
    func completeThoughtDoesNotWaitForEOF() {
        var chunker = SpeechStreamChunker()
        let first = "Podemos revisar las opciones y elegir el siguiente paso. Primero comprobamos los datos disponibles para recomendar una solución concreta."
        let snapshot = first + " Después"
        #expect(chunker.consume(snapshot) == [first])
        #expect(chunker.consume(snapshot) == [])
        #expect(chunker.finish(snapshot + " decidimos.") == ["Después decidimos."])
    }

    @Test("Does not split titles, initials or clock abbreviations", arguments: [
        "Dr.", "Dra.", "Sr.", "A.", "p.m."
    ])
    func abbreviationsRemainWithTheirContinuation(abbreviation: String) {
        var chunker = SpeechStreamChunker()
        let text = "El texto que vamos a leer contiene una referencia que debe conservarse entera durante la reproducción de esta prueba y menciona al \(abbreviation) García"
        #expect(chunker.consume(text) == [])
        #expect(chunker.finish(text) == [text])
    }

    @Test("Keeps decimal values and quoted sentence endings intact")
    func punctuationIsNotArbitraryFragmentation() {
        var chunker = SpeechStreamChunker()
        let first = "Este informe de prueba conserva los datos necesarios para revisar el resultado. El valor sigue siendo 3.14 y el informe dice «todo está en orden»."
        #expect(chunker.consume(first + " Luego") == [first])
        #expect(chunker.finish(first + " Luego terminamos.") == ["Luego terminamos."])
        chunker.reset()
        let quoted = "La frase completa que aparece en la pantalla necesita conservar su puntuación y sus comillas sin crear cortes artificiales, y dice «podemos continuar.»"
        #expect(chunker.consume(quoted + " Después") == [quoted])
    }

    @Test("A full buffer prefers a clause over an arbitrary word")
    func fullBufferPrefersClause() {
        var chunker = SpeechStreamChunker()
        let clause = Array(repeating: "primero", count: 20).joined(separator: " ") + ","
        let rest = Array(repeating: "después", count: 28).joined(separator: " ")
        let snapshot = clause + " " + rest
        let streamed = chunker.consume(snapshot)
        #expect(streamed == [clause])
        #expect((streamed + chunker.finish(snapshot)).joined(separator: " ") == snapshot)
    }

    @Test("Short answers keep one prosodic context even with token-by-token updates")
    func incrementalDeliveryPreservesCoherentUtterances() {
        var chunker = SpeechStreamChunker()
        let text = "Aquí estoy. Dime qué necesitas y vamos al grano. Si un plan se complica, lo revisamos; no hace falta convertirlo en una misión espacial."
        var snapshot = ""
        var spoken: [String] = []
        for character in text {
            snapshot.append(character)
            spoken += chunker.consume(snapshot)
        }
        spoken += chunker.finish(text)
        #expect(spoken == [text])
        #expect(spoken.joined(separator: " ") == text)
    }
}
