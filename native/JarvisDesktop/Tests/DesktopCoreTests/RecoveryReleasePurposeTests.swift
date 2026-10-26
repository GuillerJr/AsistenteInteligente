import Foundation
import XCTest
@testable import DesktopCore

final class RecoveryReleasePurposeTests: XCTestCase {
    func testReleaseAndProfilePinsRecognizeOnlyTheTypedNewChannelEffect() throws {
        let purposes = ["release.rollback.apply", "release.update.apply", "telegram.restore.reconcile"]
        let valid = RPCValue.array(purposes.map(RPCValue.string))
        XCTAssertEqual(try RecoveryReleaseDetails.maintenancePurposes(valid), purposes)
        for invalid in [
            RPCValue.array(purposes.reversed().map(RPCValue.string)),
            .array((purposes + ["telegram.restore.reconcile"]).map(RPCValue.string)),
            .array((purposes + ["telegram.restore.pairing.confirm"]).sorted().map(RPCValue.string)),
            .array((purposes + ["telegram.unknown"]).sorted().map(RPCValue.string)),
            .array([.string("telegram.restore.reconcile")]),
        ] {
            XCTAssertThrowsError(try RecoveryReleaseDetails.maintenancePurposes(invalid))
        }
    }
}
