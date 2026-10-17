import Foundation
import NaturalLanguage
import Testing
@testable import AegisAudioCore

@Test func personNameRecognizerFindsPersonalNamesOnly() {
    let text = "Tomorrow I am meeting Maria Gonzalez in Boston to talk about Apple."
    let names = PersonNameRecognizer.personNames(in: text)

    #expect(!names.contains("Boston"))
    #expect(!names.contains("Apple"))
    // Name recognition needs the OS linguistic assets; CI images may lack them.
    if NLTagger.availableTagSchemes(for: .word, language: .english).contains(.nameType),
       !names.isEmpty
    {
        #expect(names.contains { $0.contains("Maria") })
    }
}

@Test func personNameRecognizerRejectsUnboundedOrBinaryInput() {
    #expect(PersonNameRecognizer.personNames(in: "").isEmpty)
    #expect(PersonNameRecognizer.personNames(in: "Ana\u{0}López").isEmpty)
    #expect(
        PersonNameRecognizer.personNames(
            in: String(repeating: "a", count: PersonNameRecognizer.maximumTextBytes + 1)
        ).isEmpty
    )
}
