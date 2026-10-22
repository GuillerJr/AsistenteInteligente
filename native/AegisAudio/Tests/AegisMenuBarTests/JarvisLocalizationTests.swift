import Foundation
import XCTest
@testable import AegisMenuBar

final class JarvisLocalizationTests: XCTestCase {
    func testSpanishCatalogIsBundledAndFormatsNumbersAndUserText() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "Localizable", withExtension: "strings",
                                                 subdirectory: nil, localization: "es"))
        let data = try Data(contentsOf: url)
        let table = try XCTUnwrap(try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String])
        XCTAssertEqual(table["Guardar"], "Guardar")
        XCTAssertEqual(table["Paso %lld de 5"], "Paso %lld de 5")
        let step = 2
        XCTAssertEqual(String(localized: "Paso \(step) de 5", bundle: .module, locale: Locale(identifier: "es")),
                       "Paso 2 de 5")
        // Owner data is an interpolation argument, never a key looked up in the catalog.
        let title = "Guardado %d \"Cancelar\""
        XCTAssertEqual(String(localized: "Borrar «\(title)»", bundle: .module, locale: Locale(identifier: "es")),
                       "Borrar «Guardado %d \"Cancelar\"»")
    }
}
