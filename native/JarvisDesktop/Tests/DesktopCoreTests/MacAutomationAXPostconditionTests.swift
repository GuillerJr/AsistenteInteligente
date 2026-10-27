import XCTest
@testable import DesktopCore

@MainActor
final class MacAutomationAXPostconditionTests: XCTestCase {
    func testMissingAcknowledgementUsesOnlyExactPostconditionWithoutResending() async throws {
        var sends = 0, reads = 0, checks = 0
        let effect = try await MacAutomationAXDriver.performAXActionOnce(verifyRequested: true,
            dispatch: { sends += 1; return false }, readback: {
                try await MacAutomationAXDriver.confirmPostcondition(expected: "CONTADOR 1", read: {
                    reads += 1; return reads == 3 ? "CONTADOR 1" : "CONTADOR 0"
                }, beforeRead: { checks += 1 }, pause: {})
            })
        XCTAssertEqual(sends, 1)
        XCTAssertEqual(reads, 3); XCTAssertEqual(checks, 3)
        XCTAssertTrue(effect.verified); XCTAssertEqual(effect.effectStatus, "known")
        XCTAssertEqual(effect.nativeAcknowledged, false)
        XCTAssertEqual(effect.verification, "estado_postcondicion_AX_coincide")
    }

    func testNonmatchingReadbackStaysUnknownWithEitherNativeAcknowledgement() async throws {
        for acknowledged in [false, true] {
            var sends = 0, reads = 0
            let effect = try await MacAutomationAXDriver.performAXActionOnce(verifyRequested: true,
                dispatch: { sends += 1; return acknowledged }, readback: {
                    try await MacAutomationAXDriver.confirmPostcondition(expected: "CONTADOR 1", read: {
                        reads += 1; return "CONTADOR 0"
                    }, beforeRead: {}, pause: {})
                })
            XCTAssertEqual(sends, 1); XCTAssertEqual(reads, 8)
            XCTAssertFalse(effect.verified); XCTAssertEqual(effect.effectStatus, "unknown")
            XCTAssertEqual(effect.nativeAcknowledged, acknowledged)
        }
    }

    func testReadFailureAfterTheOnlyDispatchCannotBecomeKnown() async {
        var sends = 0, reads = 0
        do {
            _ = try await MacAutomationAXDriver.performAXActionOnce(verifyRequested: true,
                dispatch: { sends += 1; return false }, readback: {
                    reads += 1
                    throw MacAutomationFailure("observation_unavailable")
                })
            XCTFail("A lost readback is an uncertain dispatched effect")
        } catch {
            XCTAssertEqual(error as? MacAutomationFailure, MacAutomationFailure("result_unconfirmed", dispatched: true))
        }
        XCTAssertEqual(sends, 1); XCTAssertEqual(reads, 1)
    }

    func testCancellationOrRevocationPreventsEvenAMatchingReadbackFromPromotingTheResult() async {
        for code in ["cancelled", "authorization_unconfirmed", "target_changed", "secure_element"] {
            var sends = 0, reads = 0
            do {
                _ = try await MacAutomationAXDriver.performAXActionOnce(verifyRequested: true,
                    dispatch: { sends += 1; return true }, readback: {
                        try await MacAutomationAXDriver.confirmPostcondition(expected: "CONTADOR 1", read: {
                            reads += 1; return "CONTADOR 1"
                        }, beforeRead: { throw MacAutomationFailure(code) }, pause: {})
                    })
                XCTFail("An invalid lease, target or secure ref cannot verify")
            } catch {
                XCTAssertEqual(error as? MacAutomationFailure, MacAutomationFailure("result_unconfirmed", dispatched: true))
            }
            XCTAssertEqual(sends, 1); XCTAssertEqual(reads, 0)
        }
    }

    func testNoPostconditionKeepsTheNativeAcknowledgementContractAndNeverReads() async throws {
        var sends = 0, reads = 0
        let accepted = try await MacAutomationAXDriver.performAXActionOnce(verifyRequested: false,
            dispatch: { sends += 1; return true }, readback: { reads += 1; return true })
        XCTAssertEqual(accepted.effectStatus, "known"); XCTAssertFalse(accepted.verified)
        XCTAssertEqual(accepted.nativeAcknowledged, true)
        do {
            _ = try await MacAutomationAXDriver.performAXActionOnce(verifyRequested: false,
                dispatch: { sends += 1; return false }, readback: { reads += 1; return true })
            XCTFail("No ACK and no postcondition cannot verify a dispatched action")
        } catch {
            XCTAssertEqual(error as? MacAutomationFailure, MacAutomationFailure("native_action_unconfirmed", dispatched: true))
        }
        XCTAssertEqual(sends, 2, "Each independent operation dispatched once")
        XCTAssertEqual(reads, 0)
    }
}
