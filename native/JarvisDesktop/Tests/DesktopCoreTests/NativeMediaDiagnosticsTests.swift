import CoreGraphics
import CoreML
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision
import XCTest
@testable import DesktopCore

private struct PretendedObjCError: Error, CustomNSError {
    static var errorDomain: String { "Foundation._GenericObjCError" }
    var errorCode: Int { 0 }
    var errorUserInfo: [String: Any] { [NSLocalizedDescriptionKey: "PRIVATE-PRETENDED-ERROR"] }
}

private enum VisionReferenceIssue: Error { case cpuUnavailable, imageUnavailable }

private enum VisionRecognitionProbe: String, CaseIterable, Sendable {
    case accurateDefault = "accurate_default", fastDefault = "fast_default"
    case accurateRevision3 = "accurate_revision3"

    var level: VNRequestTextRecognitionLevel { self == .fastDefault ? .fast : .accurate }
    var languagePhase: NativeMediaDiagnostics.Phase {
        switch self {
        case .accurateDefault: .referenceAccurateLanguages
        case .fastDefault: .referenceFastLanguages
        case .accurateRevision3: .referenceRevision3Languages
        }
    }
    var performPhase: NativeMediaDiagnostics.Phase {
        switch self {
        case .accurateDefault: .referenceAccuratePerform
        case .fastDefault: .referenceFastPerform
        case .accurateRevision3: .referenceRevision3Perform
        }
    }
    var completionPhase: NativeMediaDiagnostics.Phase {
        switch self {
        case .accurateDefault: .referenceAccurateCompletion
        case .fastDefault: .referenceFastCompletion
        case .accurateRevision3: .referenceRevision3Completion
        }
    }
}

/// Vision invokes completion before perform returns, on its execution queue.
/// All shared witness state is protected by the same lock; no text is retained.
private final class VisionCompletionWitness: @unchecked Sendable {
    private let lock = NSLock()
    private var called = false
    private var hadError = false
    private var hasResults = false

    func record(hadError: Bool, hasResults: Bool) {
        lock.lock(); defer { lock.unlock() }
        called = true
        self.hadError = self.hadError || hadError
        self.hasResults = self.hasResults || hasResults
    }

    func snapshot() -> (called: Bool, hadError: Bool, hasResults: Bool) {
        lock.lock(); defer { lock.unlock() }
        return (called, hadError, hasResults)
    }
}

/// Test-only schema. Integers and booleans cannot carry OCR text or error info.
private enum VisionProbeRecord {
    static func payload(configuration: VisionRecognitionProbe, defaultRevision: Int,
                        currentRevision: Int, requestRevision: Int, supportedRevisions: [Int],
                        languageQuerySucceeded: Bool, localeSupported: Bool, performSucceeded: Bool,
                        completion: (called: Bool, hadError: Bool, hasResults: Bool),
                        recognizedMarker: Bool) -> Data? {
        guard [defaultRevision, currentRevision, requestRevision].allSatisfy({ (0...16).contains($0) }) else {
            return nil
        }
        let record: [String: Any] = [
            "format": "jarvis.native-media-level-probe/1", "configuration": configuration.rawValue,
            "default_revision": defaultRevision, "current_revision": currentRevision,
            "requested_revision": requestRevision,
            "supported_revisions": Array(Set(supportedRevisions).intersection([1, 2, 3])).sorted(),
            "language_query_succeeded": languageQuerySucceeded, "locale_supported": localeSupported,
            "perform_succeeded": performSucceeded, "callback_called": completion.called,
            "callback_had_error": completion.hadError, "callback_has_results": completion.hasResults,
            "recognized_marker": recognizedMarker,
        ]
        guard var data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else { return nil }
        data.append(10)
        return data.count <= 1024 ? data : nil
    }

    static func emit(_ data: Data?) {
        guard NativeMediaDiagnostics.enabled(environment: ProcessInfo.processInfo.environment), let data else { return }
        try? FileHandle.standardError.write(contentsOf: data)
    }
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

    func testIndependentAppleVisionReferenceComparesAutomaticAndExplicitCPUOnSameBitmap() throws {
        let image = try canonicalReferenceImage()
        let png = try canonicalReferencePNG(image)
        let formats: [NativeMediaDiagnostics.ReferenceSourceFormat] = [.cgImage, .pngData]
        for sourceFormat in formats {
            let modern = VNRecognizeTextRequest(), legacy = VNRecognizeTextRequest(), automatic = VNRecognizeTextRequest()
            XCTAssertEqual(modern.revision, legacy.revision)
            XCTAssertEqual(modern.revision, automatic.revision)
            let variants: [(NativeMediaDiagnostics.ReferenceVariant, VNRecognizeTextRequest, Bool?)] = [
                (.modern, modern, false), (.legacy, legacy, true), (.automatic, automatic, nil),
            ]
            for (variant, request, legacyCPU) in variants {
                request.recognitionLevel = .accurate
                request.recognitionLanguages = ["en-US"]
                request.usesLanguageCorrection = false
                do {
                    // Explicit CPU controls differ only in the legacy flag.
                    // Automatic uses untouched Apple compute selection, with
                    // no stage query, device assignment or legacy switch.
                    if let legacyCPU {
                        request.usesCPUOnly = legacyCPU
                        let supported: [VNComputeStage: [MLComputeDevice]] = try request.supportedComputeStageDevices
                        guard !supported.isEmpty else { throw VisionReferenceIssue.cpuUnavailable }
                        for (stage, devices) in supported {
                            guard let cpu = devices.first(where: { if case .cpu = $0 { return true }; return false }) else {
                                throw VisionReferenceIssue.cpuUnavailable
                            }
                            request.setComputeDevice(cpu, for: stage)
                        }
                    }
                    let handler: VNImageRequestHandler
                    switch sourceFormat {
                    case .cgImage: handler = VNImageRequestHandler(cgImage: image, options: [:])
                    case .pngData: handler = VNImageRequestHandler(data: png, options: [:])
                    }
                    try handler.perform([request])
                    let text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
                    let recognized = text.contains("4826")
                    NativeMediaDiagnostics.emitReferenceOutcome(variant, sourceFormat: sourceFormat,
                                                               recognizedMarker: recognized)
                    if variant == .legacy {
                        XCTAssertTrue(recognized, "La referencia Apple CPU debe reconocer el marcador real")
                    } else if variant == .automatic {
                        XCTAssertTrue(recognized, "La referencia Apple automática debe reconocer el marcador real")
                    }
                } catch {
                    NativeMediaDiagnostics.emit(variant.failurePhase, error: error, sourceFormat: sourceFormat)
                    NativeMediaDiagnostics.emitReferenceOutcome(variant, sourceFormat: sourceFormat,
                                                               recognizedMarker: false)
                    if variant == .legacy { XCTFail("La referencia Apple CPU no pudo confirmar el marcador real") }
                    else if variant == .automatic { XCTFail("La referencia Apple automática no pudo confirmar el marcador real") }
                }
            }
        }
    }

    func testIndependentAutomaticVisionLevelsExposeCompletionAndRevisionEvidence() throws {
        let image = try canonicalReferenceImage()
        let initialRevision = VNRecognizeTextRequest().revision
        let defaultRevision = VNRecognizeTextRequest.defaultRevision
        let currentRevision = VNRecognizeTextRequest.currentRevision
        let supported = Array(VNRecognizeTextRequest.supportedRevisions)
        var configurations: [VisionRecognitionProbe] = [.accurateDefault, .fastDefault]
        // Avoid a duplicate control when the runtime already defaults to 3.
        if initialRevision != 3, supported.contains(3) { configurations.append(.accurateRevision3) }
        for configuration in configurations {
            let witness = VisionCompletionWitness()
            let request = VNRecognizeTextRequest { completed, error in
                witness.record(hadError: error != nil, hasResults: !(completed.results?.isEmpty ?? true))
                if let error {
                    NativeMediaDiagnostics.emit(configuration.completionPhase, error: error, sourceFormat: .cgImage)
                }
            }
            if configuration == .accurateRevision3 { request.revision = 3 }
            else {
                guard request.revision == initialRevision else {
                    XCTFail("La comparación de niveles exige la misma revisión inicial")
                    continue
                }
            }
            request.recognitionLevel = configuration.level
            request.usesLanguageCorrection = false
            // No legacy CPU flag, stage query or device assignment is used.
            var languageQuerySucceeded = false, localeSupported = false
            var performSucceeded = false, recognizedMarker = false
            do {
                let languages = try request.supportedRecognitionLanguages()
                languageQuerySucceeded = true
                localeSupported = languages.contains("en-US")
                XCTAssertTrue(localeSupported, "La referencia debe admitir el idioma exacto del marcador")
            } catch {
                NativeMediaDiagnostics.emit(configuration.languagePhase, error: error, sourceFormat: .cgImage)
                XCTFail("La consulta de idiomas de la referencia Apple falló")
            }
            if languageQuerySucceeded, localeSupported {
                request.recognitionLanguages = ["en-US"]
                do {
                    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
                    performSucceeded = true
                    recognizedMarker = (request.results ?? []).contains {
                        $0.topCandidates(1).first?.string.contains("4826") == true
                    }
                    XCTAssertTrue(recognizedMarker, "Cada nivel de la referencia debe reconocer el marcador real")
                } catch {
                    NativeMediaDiagnostics.emit(configuration.performPhase, error: error, sourceFormat: .cgImage)
                    XCTFail("El nivel de la referencia Apple no pudo confirmar el marcador real")
                }
            }
            let completion = witness.snapshot()
            if performSucceeded {
                XCTAssertTrue(completion.called, "Vision debe entregar la finalización antes de devolver perform")
                XCTAssertFalse(completion.hadError)
                if recognizedMarker { XCTAssertTrue(completion.hasResults) }
            }
            let record = VisionProbeRecord.payload(configuration: configuration,
                defaultRevision: defaultRevision, currentRevision: currentRevision,
                requestRevision: request.revision, supportedRevisions: supported,
                languageQuerySucceeded: languageQuerySucceeded, localeSupported: localeSupported,
                performSucceeded: performSucceeded, completion: completion, recognizedMarker: recognizedMarker)
            XCTAssertNotNil(record, "El diagnóstico de la referencia debe seguir acotado y cerrado")
            VisionProbeRecord.emit(record)
        }
    }

    func testRecognitionLevelProbeRecordsOnlyClosedConfigurationsRevisionsAndBooleans() throws {
        let keys: Set<String> = ["format", "configuration", "default_revision", "current_revision",
            "requested_revision", "supported_revisions", "language_query_succeeded", "locale_supported",
            "perform_succeeded", "callback_called", "callback_had_error", "callback_has_results", "recognized_marker"]
        for configuration in VisionRecognitionProbe.allCases {
            let data = try XCTUnwrap(VisionProbeRecord.payload(configuration: configuration,
                defaultRevision: 3, currentRevision: 3, requestRevision: 3,
                supportedRevisions: [-1, 1, 2, 3, 3, 999], languageQuerySucceeded: true,
                localeSupported: true, performSucceeded: false,
                completion: (true, true, false), recognizedMarker: false))
            XCTAssertLessThanOrEqual(data.count, 1024)
            let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(Set(record.keys), keys)
            XCTAssertEqual(record["format"] as? String, "jarvis.native-media-level-probe/1")
            XCTAssertEqual(record["configuration"] as? String, configuration.rawValue)
            XCTAssertEqual(record["supported_revisions"] as? [Int], [1, 2, 3])
            XCTAssertEqual(record["callback_called"] as? Bool, true)
            XCTAssertEqual(record["callback_had_error"] as? Bool, true)
            XCTAssertEqual(record["callback_has_results"] as? Bool, false)
            XCTAssertEqual(record["recognized_marker"] as? Bool, false)
            for phase in [configuration.languagePhase, configuration.performPhase, configuration.completionPhase] {
                let failure = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: phase,
                    error: NSError(domain: "PRIVATE-PROBE-DOMAIN", code: 42,
                                   userInfo: [NSLocalizedDescriptionKey: "PRIVATE-PROBE-TEXT-PATH"]),
                    sourceFormat: .cgImage))
                let failureRecord = try XCTUnwrap(JSONSerialization.jsonObject(with: failure) as? [String: Any])
                XCTAssertEqual(failureRecord["phase"] as? String, phase.rawValue)
                XCTAssertFalse(String(decoding: failure, as: UTF8.self).contains("PRIVATE-PROBE"))
            }
        }
        for revision in [-1, 17, Int.max] {
            XCTAssertNil(VisionProbeRecord.payload(configuration: .accurateDefault,
                defaultRevision: revision, currentRevision: 3, requestRevision: 3,
                supportedRevisions: [1, 2, 3], languageQuerySucceeded: false, localeSupported: false,
                performSucceeded: false, completion: (false, false, false), recognizedMarker: false))
        }
        let witness = VisionCompletionWitness()
        XCTAssertFalse(witness.snapshot().called)
        witness.record(hadError: true, hasResults: false)
        witness.record(hadError: false, hasResults: true)
        let captured = witness.snapshot()
        XCTAssertTrue(captured.called); XCTAssertTrue(captured.hadError); XCTAssertTrue(captured.hasResults)
    }

    private func canonicalReferenceImage() throws -> CGImage {
        let space = try XCTUnwrap(CGColorSpace(name: CGColorSpace.sRGB))
        let info = CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
        let context = try XCTUnwrap(CGContext(data: nil, width: 1200, height: 300,
            bitsPerComponent: 8, bytesPerRow: 1200 * 4, space: space, bitmapInfo: info))
        context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 1200, height: 300))
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 44, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1),
        ]
        let marker = NSAttributedString(string: "JARVIS LOCAL IMAGE 4826", attributes: attributes)
        context.textMatrix = .identity
        context.textPosition = CGPoint(x: 50, y: 130)
        CTLineDraw(CTLineCreateWithAttributedString(marker), context)
        return try XCTUnwrap(context.makeImage())
    }

    private func canonicalReferencePNG(_ image: CGImage) throws -> Data {
        let data = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(data as CFMutableData,
            UTType.png.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            XCTFail("No se pudo crear el PNG canónico de control")
            throw VisionReferenceIssue.imageUnavailable
        }
        return data as Data
    }

    func testReferenceResultsExposeOnlyClosedVariantSourceFormatAndMarkerBoolean() throws {
        let variants: [NativeMediaDiagnostics.ReferenceVariant] = [.modern, .legacy, .automatic]
        let formats: [NativeMediaDiagnostics.ReferenceSourceFormat] = [.cgImage, .pngData]
        for sourceFormat in formats {
            for variant in variants {
                for recognized in [false, true] {
                    let data = try XCTUnwrap(NativeMediaDiagnostics.referencePayload(variant: variant,
                        sourceFormat: sourceFormat, recognizedMarker: recognized))
                    XCTAssertLessThanOrEqual(data.count, 1024)
                    let record = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
                    XCTAssertEqual(Set(record.keys), Set(["format", "variant", "source_format", "recognized_marker"]))
                    XCTAssertEqual(record["format"] as? String, "jarvis.native-media-reference/1")
                    XCTAssertEqual(record["variant"] as? String, variant.rawValue)
                    XCTAssertEqual(record["source_format"] as? String, sourceFormat.rawValue)
                    XCTAssertEqual(record["recognized_marker"] as? Bool, recognized)
                    let failure = try XCTUnwrap(NativeMediaDiagnostics.payload(phase: variant.failurePhase,
                        error: NSError(domain: "PRIVATE-DOMAIN", code: 0), sourceFormat: sourceFormat))
                    let failureRecord = try XCTUnwrap(JSONSerialization.jsonObject(with: failure) as? [String: Any])
                    XCTAssertEqual(Set(failureRecord.keys), Set(["format", "phase", "source_format", "errors"]))
                    XCTAssertEqual(failureRecord["source_format"] as? String, sourceFormat.rawValue)
                    XCTAssertFalse(String(decoding: failure, as: UTF8.self).contains("PRIVATE-DOMAIN"))
                }
            }
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
