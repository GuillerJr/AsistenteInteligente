import Foundation
import Testing
@testable import AegisAudioCore

@Test func personNameRecognizerFindsPersonalNamesOnly() {
    let names = PersonNameRecognizer.personNames(
        in: "Mañana me reúno con María González en Quito para hablar de Apple."
    )

    #expect(names.contains { $0.contains("María") })
    #expect(!names.contains("Quito"))
    #expect(!names.contains("Apple"))
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
