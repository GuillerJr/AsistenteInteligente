import Foundation
import XCTest
@testable import DesktopCore

private struct PretendedObjCError: Error, CustomNSError {
    static var errorDomain: String { "Foundation._GenericObjCError" }
    var errorCode: Int { 0 }
    var errorUserInfo: [String: Any] { [NSLocalizedDescriptionKey: "PRIVATE-PRETENDED-ERROR"] }
}

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
        let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .imageSource, error: original))
        XCTAssertLessThanOrEqual(data.count, 1024)
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))
        XCTAssertFalse(text.contains(marker)); XCTAssertFalse(text.contains("987654321"))
        XCTAssertFalse(text.contains("userInfo")); XCTAssertFalse(text.contains("example.invalid"))
        let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(Set(record.keys), Set(["format", "phase", "errors"]))
        XCTAssertEqual(record["format"] as? String, "jarvis.native-media-diagnostic/1")
        XCTAssertEqual(record["phase"] as? String, "image.source")
        let errors = try XCTUnwrap(record["errors"] as? [[String: Any]])
        XCTAssertEqual(errors.count, 3)
        XCTAssertEqual(errors.compactMap { $0["domain"] as? String }, ["other", NSPOSIXErrorDomain, "other"])
        XCTAssertEqual(errors.compactMap { $0["code"] as? Int }, [41, 42, 43])
        XCTAssertEqual(errors.compactMap { $0["kind"] as? String }, ["other", "other", "other"])
        for error in errors { XCTAssertEqual(Set(error.keys), Set(["domain", "code", "kind"])) }
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
        XCTAssertEqual(errors.first?["kind"] as? String, "other")
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("PRIVATE-DECODER-MESSAGE"))
    }

    func testRealFoundationNilErrorAndCancellationHaveClosedKindsWithoutExposingTypes() throws {
        // The actual Foundation bridge creates this error; no imitation domain
        // or private SPI is added to the product diagnostic implementation.
        let missing = Foundation._convertNSErrorToError(nil)
        let knownAliases: Set<String> = [
            "Foundation._GenericObjCError", "FoundationEssentials._GenericObjCError", "Swift._GenericObjCError",
        ]
        XCTAssertTrue(knownAliases.contains(String(reflecting: type(of: missing))),
                      "El puente real debe usar un alias cerrado conocido; no registrar tipos desconocidos")
        let cases: [(Error, NativeMediaDiagnostics.ErrorKind)] = [
            (missing, .objcMissingError), (CancellationError(), .cancellationError),
        ]
        for (error, expected) in cases {
            XCTAssertEqual(NativeMediaDiagnostics.errorKind(error), expected)
            let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .pdfRaster, error: error))
            let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(record["phase"] as? String, "pdf.raster")
            let errors = try XCTUnwrap(record["errors"] as? [[String: Any]])
            XCTAssertEqual(errors.first?["kind"] as? String, expected.rawValue)
            let text = String(decoding: data, as: UTF8.self)
            XCTAssertFalse(text.contains("_GenericObjCError")); XCTAssertFalse(text.contains("CancellationError"))
            XCTAssertLessThanOrEqual(data.count, 1024)
        }
    }

    func testPretendedNSErrorDomainsAndCustomErrorsCannotClaimKnownConcreteTypes() throws {
        let domains = ["Foundation._GenericObjCError", "FoundationEssentials._GenericObjCError",
                       "Swift._GenericObjCError", "Swift.CancellationError", "_Concurrency.CancellationError"]
        var errors: [Error] = domains.map { domain -> Error in
            NSError(domain: domain, code: 0, userInfo: [NSLocalizedDescriptionKey: "PRIVATE-PRETENDED-ERROR"])
        }
        errors.append(PretendedObjCError())
        for error in errors {
            XCTAssertEqual(NativeMediaDiagnostics.errorKind(error), .other)
            let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .videoSample, error: error))
            let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let rows = try XCTUnwrap(record["errors"] as? [[String: Any]])
            XCTAssertEqual(rows.first?["kind"] as? String, "other")
            XCTAssertEqual(rows.first?["domain"] as? String, "other")
            let text = String(decoding: data, as: UTF8.self)
            XCTAssertFalse(text.contains("_GenericObjCError")); XCTAssertFalse(text.contains("CancellationError"))
            XCTAssertFalse(text.contains("PretendedObjCError")); XCTAssertFalse(text.contains("PRIVATE-PRETENDED-ERROR"))
        }
    }

    private func localDiagnosticAdmission() -> [String: String] {
        let nonce = String(repeating: "a", count: 48)
        return ["JARVIS_TEST_GUARD_PROFILE": "local",
                "JARVIS_TEST_GUARD_SELECTION": "NativeMedia(Executor|Diagnostics)Tests",
                "JARVIS_TEST_GUARD_NATIVE_MEDIA_FRAMEWORKS": "1",
                "JARVIS_TEST_GUARD_NONCE": nonce, "JARVIS_TEST_GUARD_CHILD_CHECKED": nonce]
    }

    func testDiagnosticsRequireDebugCIOrExplicitlyAdmittedNativeMediaContext() {
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: [:]))
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "false"]))
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "TRUE"]))
        #if DEBUG
        XCTAssertTrue(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "true"]))
        XCTAssertTrue(NativeMediaDiagnostics.enabled(environment: localDiagnosticAdmission()))
        #else
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: ["GITHUB_ACTIONS": "true"]))
        XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: localDiagnosticAdmission()))
        #endif
    }

    func testLocalDiagnosticsRejectBareOrMismatchedNativeMediaAdmission() {
        let admitted = localDiagnosticAdmission()
        for key in admitted.keys {
            var missing = admitted
            missing.removeValue(forKey: key)
            XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: missing))
        }
        let mismatches: [(String, String)] = [
            ("JARVIS_TEST_GUARD_PROFILE", "ci"),
            ("JARVIS_TEST_GUARD_SELECTION", "NativeMediaExecutorTests"),
            ("JARVIS_TEST_GUARD_SELECTION", "NativeMedia.*"),
            ("JARVIS_TEST_GUARD_SELECTION", "NativeMedia(Executor|Diagnostics)Tests "),
            ("JARVIS_TEST_GUARD_NATIVE_MEDIA_FRAMEWORKS", "true"),
            ("JARVIS_TEST_GUARD_CHILD_CHECKED", String(repeating: "b", count: 48)),
        ]
        for (key, value) in mismatches {
            var rejected = admitted
            rejected[key] = value
            XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: rejected))
        }
        for invalid in [String(repeating: "a", count: 47), String(repeating: "a", count: 49),
                        String(repeating: "A", count: 48), String(repeating: "g", count: 48)] {
            var rejected = admitted
            rejected["JARVIS_TEST_GUARD_NONCE"] = invalid
            rejected["JARVIS_TEST_GUARD_CHILD_CHECKED"] = invalid
            XCTAssertFalse(NativeMediaDiagnostics.enabled(environment: rejected))
        }
    }
}
