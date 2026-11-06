import Foundation
import XCTest
@testable import DesktopCore

final class ProviderLimitsTests: XCTestCase {
    private func decode(_ text: String) throws -> RPCValue {
        try JSONDecoder().decode(RPCValue.self, from: Data(text.utf8))
    }

    func testSnapshotKeepsUnknownFieldsUnknownInsteadOfZero() throws {
        let value = try decode("""
        {"connected":true,"updated_at":1800000000,"limits":[{"limit_id":"codex","name":null,
         "plan":"plus","reached":null,"credits":null,
         "windows":{"primary":{"used_percent":42.4,"resets_at":1800003600,"window_minutes":300},
                    "secondary":{"used_percent":null,"resets_at":null,"window_minutes":10080}}}]}
        """)
        let limits = try XCTUnwrap(ProviderLimit.snapshot(value))
        XCTAssertEqual(limits.map(\.id), ["codex"])
        XCTAssertEqual(limits[0].plan, "plus")
        XCTAssertNil(limits[0].creditBalance)
        XCTAssertEqual(limits[0].primary?.usedPercent, 42.4)
        XCTAssertNil(limits[0].secondary?.usedPercent)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        XCTAssertEqual(ProviderLimit.describe(try XCTUnwrap(limits[0].secondary), now: now),
                       "Ventana de 7 d, uso desconocido")
        XCTAssertTrue(ProviderLimit.describe(try XCTUnwrap(limits[0].primary), now: now)
            .hasPrefix("Ventana de 5 h, uso 42 %, se renueva"))
    }

    func testMalformedDuplicatedOrDisconnectedRowsRejectTheWholeReply() throws {
        let row = #"{"limit_id":"codex","name":null,"plan":null,"reached":null,"credits":null,"windows":{"primary":null,"secondary":null}}"#
        XCTAssertNil(ProviderLimit.snapshot(try decode(#"{"connected":true,"limits":[\#(row),\#(row)]}"#)))
        XCTAssertNil(ProviderLimit.snapshot(try decode(#"{"connected":false,"limits":[\#(row)]}"#)))
        XCTAssertNil(ProviderLimit.snapshot(try decode(#"{"connected":true,"limits":[{"limit_id":"","windows":{}}]}"#)))
        XCTAssertEqual(ProviderLimit.snapshot(try decode(#"{"connected":false,"limits":[]}"#)), [])
    }
}
