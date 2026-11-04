import CoreML
import Foundation
import Vision
import XCTest
@testable import DesktopCore

private struct PretendedObjCError: Error, CustomNSError {
    static var errorDomain: String { "Foundation._GenericObjCError" }
    var errorCode: Int { 0 }
    var errorUserInfo: [String: Any] { [NSLocalizedDescriptionKey: "PRIVATE-PRETENDED-ERROR"] }
}

private enum VisionReferenceIssue: Error { case cpuUnavailable }

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
            let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .visionPerform, error: error))
            let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
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
            let data = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: .visionPerform, error: error))
            let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let rows = try XCTUnwrap(record["errors"] as? [[String: Any]])
            XCTAssertEqual(rows.first?["kind"] as? String, "other")
            XCTAssertEqual(rows.first?["domain"] as? String, "other")
            let text = String(decoding: data, as: UTF8.self)
            XCTAssertFalse(text.contains("_GenericObjCError")); XCTAssertFalse(text.contains("CancellationError"))
            XCTAssertFalse(text.contains("PretendedObjCError")); XCTAssertFalse(text.contains("PRIVATE-PRETENDED-ERROR"))
        }
    }

    func testIndependentAppleVisionReferenceComparesOnlyLegacyCPUFlagOnSameBitmap() throws {
        let image = try NativeMediaFixtures.picture("JARVIS LOCAL IMAGE 4826")
        let modern = VNRecognizeTextRequest(), legacy = VNRecognizeTextRequest()
        XCTAssertEqual(modern.revision, legacy.revision)
        let variants: [(NativeMediaDiagnostics.ReferenceVariant, VNRecognizeTextRequest, Bool)] = [
            (.modern, modern, false), (.legacy, legacy, true),
        ]
        for (variant, request, legacyCPU) in variants {
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["en-US"]
            request.usesLanguageCorrection = false
            // The requests share the exact bitmap and per-stage CPU policy.
            // Only this public legacy flag differs; no product helper runs.
            request.usesCPUOnly = legacyCPU
            do {
                let supported: [VNComputeStage: [MLComputeDevice]] = try request.supportedComputeStageDevices
                guard !supported.isEmpty else { throw VisionReferenceIssue.cpuUnavailable }
                for (stage, devices) in supported {
                    guard let cpu = devices.first(where: { if case .cpu = $0 { return true }; return false }) else {
                        throw VisionReferenceIssue.cpuUnavailable
                    }
                    request.setComputeDevice(cpu, for: stage)
                }
                try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
                let text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
                let recognized = text.contains("4826")
                NativeMediaDiagnostics.emitReferenceOutcome(variant, recognizedMarker: recognized)
                if variant == .legacy {
                    XCTAssertTrue(recognized, "La referencia Apple CPU debe reconocer el marcador real")
                }
            } catch {
                NativeMediaDiagnostics.emit(variant.failurePhase, error: error)
                NativeMediaDiagnostics.emitReferenceOutcome(variant, recognizedMarker: false)
                if variant == .legacy { throw error }
            }
        }
    }

    func testReferenceResultsExposeOnlyClosedVariantAndMarkerBoolean() throws {
        let variants: [NativeMediaDiagnostics.ReferenceVariant] = [.modern, .legacy]
        for variant in variants {
            for recognized in [false, true] {
                let data = try XCTUnwrap(NativeMediaDiagnostics.referencePayload(variant: variant, recognizedMarker: recognized))
                XCTAssertLessThanOrEqual(data.count, 1024)
                let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
                XCTAssertEqual(Set(record.keys), Set(["format", "variant", "recognized_marker"]))
                XCTAssertEqual(record["format"] as? String, "jarvis.native-media-reference/1")
                XCTAssertEqual(record["variant"] as? String, variant.rawValue)
                XCTAssertEqual(record["recognized_marker"] as? Bool, recognized)
            }
        }
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
