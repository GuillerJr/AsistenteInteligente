import Foundation
import XCTest
@testable import DesktopCore

final class NativeMediaDiagnosticsTests: XCTestCase {
    func testOnlyClosedPhaseWhitelistedDomainsAndCodesSurvivePrivateErrorInfo() throws {
        let marker = "PRIVATE-MEDIA-TEXT-PATH-URL-NEVER-LOG"
        let fourth = NSError(domain: NSCocoaErrorDomain, code: 987654321,
                             userInfo: [NSLocalizedDescriptionKey: marker])
        let third = NSError(domain: marker + ".nested-domain", code: 43,
                            userInfo: [NSUnderlyingErrorKey: fourth, "arbitrary": marker])
        let second = NSError(domain: NSPOSIXErrorDomain, code: 42,
                             userInfo: [NSUnderlyingErrorKey: third, NSLocalizedFailureReasonErrorKey: marker])
        let original = NSError(domain: marker + ".domain", code: 41, userInfo: [
            NSUnderlyingErrorKey: second, NSLocalizedDescriptionKey: marker,
            NSFilePathErrorKey: "/private/" + marker,
            NSURLErrorKey: URL(string: "https://example.invalid/" + marker)!,
            "arbitrary": ["payload": marker],
        ])
        let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .visionPerform, error: original))
        XCTAssertLessThanOrEqual(data.count, 1024)
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))
        XCTAssertFalse(text.contains(marker)); XCTAssertFalse(text.contains("987654321"))
        XCTAssertFalse(text.contains("userInfo")); XCTAssertFalse(text.contains("example.invalid"))
        let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(Set(record.keys), Set(["format", "phase", "errors"]))
        XCTAssertEqual(record["format"] as? String, "jarvis.native-media-diagnostic/1")
        XCTAssertEqual(record["phase"] as? String, "vision.perform")
        let errors = try XCTUnwrap(record["errors"] as? [[String: Any]])
        XCTAssertEqual(errors.count, 3)
        XCTAssertEqual(errors.compactMap { $0["domain"] as? String }, ["other", NSPOSIXErrorDomain, "other"])
        XCTAssertEqual(errors.compactMap { $0["code"] as? Int }, [41, 42, 43])
        for error in errors { XCTAssertEqual(Set(error.keys), Set(["domain", "code"])) }
    }

    func testKnownDomainAndSignedCodeArePreservedWithoutLocalizedMessages() throws {
        let error = NSError(domain: NSOSStatusErrorDomain, code: -12909,
                            userInfo: [NSLocalizedDescriptionKey: "PRIVATE-DECODER-MESSAGE"])
        let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .videoSample, error: error))
        let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let errors = try XCTUnwrap(record["errors"] as? [[String: Any]])
        XCTAssertEqual(errors.count, 1)
        XCTAssertEqual(errors.first?["domain"] as? String, NSOSStatusErrorDomain)
        XCTAssertEqual(errors.first?["code"] as? Int, -12909)
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("PRIVATE-DECODER-MESSAGE"))
    }

    func testDiagnosticsRequireBothDebugBuildAndExactGitHubActionsOptIn() {
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: [:]))
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "false"]))
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "TRUE"]))
        #if DEBUG
        XCTAssertTrue(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "true"]))
        #else
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "true"]))
        #endif
    }
}
