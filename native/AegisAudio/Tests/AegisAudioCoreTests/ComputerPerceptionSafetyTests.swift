import Testing
@testable import AegisAudioCore

private struct PerceptionFixture {
    let role: String
    let subrole: String
    let descriptor: String

    init(_ role: String, _ descriptor: String, subrole: String = "") {
        self.role = role
        self.subrole = subrole
        self.descriptor = descriptor
    }

    var disposition: ComputerControlSafety.PerceptionDisposition {
        ComputerControlSafety.perceptionDisposition(
            role: role, subrole: subrole, descriptor: descriptor
        )
    }
}

private func accumulatedSafety(_ items: [PerceptionFixture]) -> ComputerAXPerceptionSafety {
    var safety = ComputerAXPerceptionSafety()
    for item in items {
        _ = safety.observe(role: item.role, subrole: item.subrole, descriptor: item.descriptor)
    }
    return safety
}

@Test func textEditBlankDocumentKeepsItsTextFieldWithoutOfferingClearColorButtons() {
    // AppKit exposes the color well's clear value as "Borrar" in Spanish.
    // This is a formatting control elsewhere in the same new, empty document.
    let document = [
        PerceptionFixture("AXTextArea", "First Text View área de texto"),
        PerceptionFixture(
            "AXButton", "color del texto Seleccionar el color del texto Borrar"
        ),
        PerceptionFixture(
            "AXButton", "color del fondo del texto Seleccionar el color del fondo del texto Borrar"
        ),
        PerceptionFixture("AXButton", "Cerrar botón de cierre"),
    ]
    let exposed = document.filter { $0.disposition != .omit }

    #expect(exposed.map(\.descriptor) == ["First Text View área de texto"])
    #expect(!exposed.contains { $0.disposition == .sensitive })
    #expect(!accumulatedSafety(document).secureContent)
    #expect(!ComputerControlSafety.isSensitiveElementText("PRUEBA JARVIS A"))
    for control in document.dropFirst() {
        // The action boundary continues to reject these exact descriptors even
        // if a model guesses the coordinates of a control omitted from metadata.
        #expect(ComputerControlSafety.isSensitiveElementText(control.descriptor))
    }
}

@Test(arguments: ["Borrar archivo", "Eliminar documento", "Delete account", "Cerrar ventana"])
func omittedDestructiveButtonsRemainBlockedExecutionTargets(descriptor: String) {
    let button = PerceptionFixture("AXButton", descriptor)

    #expect(button.disposition == .omit)
    #expect(ComputerControlSafety.isSensitiveElementText(descriptor))
}

@Test(arguments: [
    "Contraseña", "CONTRASENA", "Iniciar sesión", "INICIAR SESION", "Password",
    "Borrar contraseña", "Cerrar token de acceso", "Eliminar API key",
    "Borrar número de tarjeta", "Cerrar código de seguridad", "Delete account number",
    "Pagar", "Borrar pago", "Delete payment", "Confirm purchase", "Permitir", "Autorizar",
])
func privateAndApprovalButtonLabelsKeepTheWholeObservationSensitive(descriptor: String) {
    let button = PerceptionFixture("AXButton", descriptor)

    #expect(button.disposition == .sensitive)
    #expect(ComputerControlSafety.isSensitiveElementText(descriptor))
}

@Test(arguments: [
    "AXTextArea", "AXTextField", "AXSearchField", "AXComboBox", "AXStaticText",
    "AXHeading", "AXMenuItem", "AXPopUpButton", "AXDialog", "AXSheet",
])
func sensitiveContentOutsideButtonsIsNeverOmitted(role: String) {
    for descriptor in ["Borrar", "Contraseña", "Borrar token de autenticación"] {
        let item = PerceptionFixture(role, descriptor)
        #expect(item.disposition == .sensitive)
    }
}

@Test func secureFieldsBlockEvenWithoutAVisibleLabelOrWithAMisleadingButtonRole() {
    for role in ["AXTextField", "AXTextArea", "AXButton"] {
        for descriptor in ["", "First Text View", "Borrar"] {
            let field = PerceptionFixture(role, descriptor, subrole: "AXSecureTextField")
            #expect(field.disposition == .sensitive)
        }
    }
}

@Test func privateContentStillBlocksNextToOmittedTextEditFormattingControls() {
    let document = [
        PerceptionFixture("AXTextArea", "First Text View Contraseña: dato sintético"),
        PerceptionFixture("AXButton", "color del texto Borrar"),
    ]
    let exposed = document.filter { $0.disposition != .omit }

    #expect(exposed.count == 1)
    #expect(exposed.contains { $0.disposition == .sensitive })
    #expect(accumulatedSafety(document).secureContent)
    #expect(ComputerControlSafety.isSensitiveElementText("Contraseña: dato sintético"))
}

@Test func privacyAggregationSurvivesAnEmptyExportedItemList() {
    // These observations may be filtered from metadata for different reasons:
    // an empty secure-field label, unavailable geometry, or an unlisted AX role.
    let excluded: [(PerceptionFixture, Bool)] = [
        (PerceptionFixture("AXTextField", "", subrole: "AXSecureTextField"), true),
        (PerceptionFixture("AXButton", "Contraseña"), false),
        (PerceptionFixture("AXGroup", "Número de tarjeta: dato sintético"), true),
    ]
    for (item, hasGeometry) in excluded {
        var safety = ComputerAXPerceptionSafety()
        #expect(safety.observe(
            role: item.role, subrole: item.subrole, descriptor: item.descriptor,
            hasUsableGeometry: hasGeometry
        ) == .omit)
        // The helper records the node before any of those metadata filters and
        // passes this independent bit through, even if no item can be exported.
        #expect(safety.secureContent)
        _ = safety.observe(role: "AXButton", subrole: "", descriptor: "Borrar")
        _ = safety.observe(role: "AXTextArea", subrole: "", descriptor: "First Text View")
        #expect(safety.secureContent)
    }
}

@Test func omittedActionControlDoesNotHideSensitiveDescendants() {
    var safety = ComputerAXPerceptionSafety()
    #expect(safety.observe(role: "AXButton", subrole: "", descriptor: "Borrar") == .omit)
    #expect(!safety.secureContent)
    #expect(safety.observe(
        role: "AXStaticText", subrole: "", descriptor: "Contraseña: dato sintético"
    ) == .sensitive)
    #expect(safety.secureContent)
}
