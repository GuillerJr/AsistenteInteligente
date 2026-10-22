import Testing
@testable import AegisAudioCore

@Test func computerAXLabelPrefersTheFirstNonemptyCanonicalName() {
    #expect(ComputerAXObservationMetadata.label(
        title: "Nombre visible", description: "Descripción", identifier: "internal-name"
    ) == "Nombre visible")
    #expect(ComputerAXObservationMetadata.label(
        title: " \n\t", description: "Descripción", identifier: "internal-name"
    ) == "Descripción")
    #expect(ComputerAXObservationMetadata.label(
        title: nil, description: "", identifier: "First Text View"
    ) == "First Text View")
    #expect(ComputerAXObservationMetadata.label(
        title: "", description: nil, identifier: " \n\t"
    ) == nil)
}

@Test func computerAXLabelNormalizesWhitespaceAndBoundsTheSelectedName() {
    #expect(ComputerAXObservationMetadata.label(
        title: " \n First\t Text\u{00a0} View \n", description: nil, identifier: nil
    ) == "First Text View")
    let longName = String(repeating: "🤖", count: 257)
    let label = ComputerAXObservationMetadata.label(
        title: longName, description: "Short description", identifier: "short-identifier"
    )
    #expect(label == String(repeating: "🤖", count: 256))
}

@Test func textEditAXLabelSurvivesTypingAndDescriptionFallbackToIdentifier() {
    // TextEdit exposes the field's name separately from the localized role and
    // its changing value. The exact postcondition target is "First Text View".
    let beforeLabel = ComputerAXObservationMetadata.label(
        title: nil, description: "First Text View", identifier: "First Text View"
    )
    let afterLabel = ComputerAXObservationMetadata.label(
        title: nil, description: nil, identifier: "First Text View"
    )
    #expect(beforeLabel == "First Text View")
    #expect(afterLabel == beforeLabel)
    #expect(beforeLabel != "First Text View área de texto")
    #expect(afterLabel != "First Text View área de texto PRUEBA JARVIS A")

    // A truly empty baseline is an observed value, not missing metadata.
    let beforeValue = ComputerAXObservationMetadata.value("")
    let afterValue = ComputerAXObservationMetadata.value("PRUEBA JARVIS A")
    #expect(beforeValue.text == "")
    #expect(!beforeValue.truncated)
    #expect(afterValue.text == "PRUEBA JARVIS A")
    #expect(!afterValue.truncated)
}

@Test func computerAXCanonicalLabelDoesNotDisambiguateIdenticallyNamedFields() {
    let labels = ["first-field", "second-field"].map { identifier in
        ComputerAXObservationMetadata.label(
            title: "Buscar", description: nil, identifier: identifier
        )
    }
    // The consumer must still see two exact candidates and reject ambiguity;
    // canonical metadata does not invent a name or add a positional suffix.
    #expect(labels == ["Buscar", "Buscar"])
}

@Test func computerAXCanonicalLabelDoesNotReplaceFullSecurityDescriptors() {
    let label = ComputerAXObservationMetadata.label(
        title: "First Text View", description: "Contraseña", identifier: "password-field"
    )
    #expect(label == "First Text View")
    let fullDescriptor = "First Text View Contraseña password-field área de texto dato sintético"
    var safety = ComputerAXPerceptionSafety()
    #expect(safety.observe(
        role: "AXTextArea", subrole: "", descriptor: fullDescriptor
    ) == .sensitive)
    #expect(safety.secureContent)
    #expect(ComputerControlSafety.isSensitiveElementText(fullDescriptor))

    // A harmless title also cannot authorize a destructive control whose help
    // or value carries the blocked action, as with TextEdit's color controls.
    let buttonDescriptor = "color del texto Seleccionar el color del texto Borrar"
    #expect(ComputerControlSafety.perceptionDisposition(
        role: "AXButton", subrole: "", descriptor: buttonDescriptor
    ) == .omit)
    #expect(ComputerControlSafety.isSensitiveElementText(buttonDescriptor))
}
