import Foundation
import XCTest
@testable import AegisMenuBar

final class JarvisPlanAndDateTests: XCTestCase {
    func testOnlyFirstNumberedListIsThePlan() {
        XCTAssertEqual(JarvisPlanCard.steps(in: """
            Este es el plan:
            1. **Revisar** los archivos

            2) Ejecutar las pruebas
            Notas:
            1. Una alternativa
            2. Otra alternativa
            """), ["Revisar los archivos", "Ejecutar las pruebas"])
    }

    func testPlanRejectsHeadingsDatesAndMissingStepText() {
        XCTAssertEqual(JarvisPlanCard.steps(in: "2026. Informe\n1.Sin espacio\n1. \n- Lista"), [])
        XCTAssertEqual(JarvisPlanCard.steps(in: "1. Revisar\nExplicación\n2. Otro bloque"), ["Revisar"])
    }

    func testDaemonDatesPreserveFractionsAndTimeZones() throws {
        let utc = try XCTUnwrap(Date(jarvisISO: "2026-10-21T15:30:00Z"))
        XCTAssertEqual(Date(jarvisISO: "2026-10-21T10:30:00-05:00"), utc)
        let fractional = try XCTUnwrap(Date(jarvisISO: "2026-10-21T15:30:00.123456+00:00"))
        XCTAssertEqual(fractional.timeIntervalSince(utc), 0.123456, accuracy: 0.001)
        XCTAssertEqual(Date(jarvisISO: "2026-10-21T10:30:00.123456-05:00"), fractional)
    }

    func testMalformedDaemonDatesAreRejected() {
        for value in ["", "mañana", "2026-10-21", "2026-10-21T15:30:00", "2026-10-21T99:00:00Z"] {
            XCTAssertNil(Date(jarvisISO: value), value)
        }
    }
}
