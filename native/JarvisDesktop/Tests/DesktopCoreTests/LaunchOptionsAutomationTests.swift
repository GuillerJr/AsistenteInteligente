import Foundation
import XCTest
@testable import DesktopCore

final class LaunchOptionsAutomationTests: XCTestCase {
    private let origin = "http://127.0.0.1:54321"
    private func base(state: String = "/private/tmp/jarvis-automation-synthetic/profile") -> [String] {
        ["--python", "/usr/bin/python3", "--source", "/private/tmp/jarvis-automation-synthetic/source",
         "--state", state, "--codex", "/usr/bin/false"]
    }

    func testExactSyntheticOriginIsNativeOnlyAndNeverEnablesItselfByDefault() throws {
        let normal = try LaunchOptions.parse(base())
        XCTAssertNil(normal.automationTestOrigin)
        for port in [1024, 54321, 65535] {
            let value = "http://127.0.0.1:\(port)"
            let options = try LaunchOptions.parse(base() + ["--automation-test-origin", value])
            XCTAssertEqual(options.automationTestOrigin, value)
            XCTAssertFalse(options.recoveryOnly)
            XCTAssertNil(options.expectedRestore)
            XCTAssertEqual(options.serviceArguments, normal.serviceArguments)
            XCTAssertFalse(options.serviceArguments.contains("--automation-test-origin"))
            XCTAssertFalse(options.serviceArguments.contains(value))
        }
    }

    func testSyntheticNetworkExceptionIsRejectedOutsideExplicitPrivateTmpState() {
        for state in ["/Users/synthetic/Jarvis", "/tmp/jarvis-test", "/private/tmp",
                      "/private/tmp-other/profile", "/private/tmp/../../Users/synthetic/profile", "relative"] {
            XCTAssertThrowsError(try LaunchOptions.parse(base(state: state)
                + ["--automation-test-origin", origin]), state)
        }
    }

    func testRecoveryCannotBorrowSyntheticNetworkOrOwnFixturePrivilege() {
        XCTAssertThrowsError(try LaunchOptions.parse(base()
            + ["--automation-test-origin", origin, "--recovery-only"]))
        XCTAssertThrowsError(try LaunchOptions.parse(base()
            + ["--recovery-only", "--automation-test-origin", origin]))
    }

    func testRestoredProfileCannotBorrowSyntheticNetworkOrOwnFixturePrivilege() throws {
        let target = try RestoreFixture.target()
        let enabled = try LaunchOptions.parse(base() + ["--automation-test-origin", origin])
        let restored = enabled.openingRestored(target)
        XCTAssertNil(restored.automationTestOrigin)
        XCTAssertEqual(restored.expectedRestore?.operationID, target.operationID)
        XCTAssertThrowsError(try LaunchOptions.parse(base(state: target.path)
            + restored.expectedRestore!.arguments + ["--automation-test-origin", origin]))
        XCTAssertThrowsError(try LaunchOptions.parse(base(state: target.path)
            + ["--automation-test-origin", origin] + restored.expectedRestore!.arguments))
    }

    func testMalformedOriginCannotRelaxSchemeHostPortOrCredentials() {
        let rejected = ["", "http://127.0.0.1", "http://127.0.0.1:80", "http://127.0.0.1:1023",
            "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:054321",
            "https://127.0.0.1:54321", "http://localhost:54321", "http://[::1]:54321",
            "http://127.0.0.2:54321", "http://127.1:54321", "http://example.com:54321",
            "http://name@127.0.0.1:54321", "http://name:secret@127.0.0.1:54321",
            origin + "/", origin + "/page", origin + "?q=x", origin + "#fragment",
            origin + " ", origin + "\n", origin + "\0", "HTTP://127.0.0.1:54321"]
        for value in rejected {
            XCTAssertThrowsError(try LaunchOptions.parse(base() + ["--automation-test-origin", value]), value)
        }
    }

    func testMissingDuplicateOrUnknownAutomationFlagsAreRejected() {
        XCTAssertThrowsError(try LaunchOptions.parse(base() + ["--automation-test-origin"]))
        XCTAssertThrowsError(try LaunchOptions.parse(base()
            + ["--automation-test-origin", origin, "--automation-test-origin", origin]))
        XCTAssertThrowsError(try LaunchOptions.parse(base() + ["--allow-own-test-window", "true"]))
        XCTAssertThrowsError(try LaunchOptions.parse(base() + ["--automation-test-origin", "--recovery-only"]))
    }
}
