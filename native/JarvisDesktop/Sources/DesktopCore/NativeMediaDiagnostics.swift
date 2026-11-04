import AVFoundation
import CoreML
import Foundation
import Vision

enum NativeMediaDiagnostics {
    enum Phase: String {
        case imageSource = "image.source"
        case imageThumbnail = "image.thumbnail"
        case visionLanguages = "vision.languages"
        case visionLocale = "vision.locale"
        case visionComputeDevices = "vision.compute_devices"
        case visionCPUAssignments = "vision.cpu_assignments"
        case visionPerform = "vision.perform"
        case referenceModern = "vision.reference_modern"
        case referenceLegacy = "vision.reference_legacy"
        case referenceAutomatic = "vision.reference_automatic"
        case referenceAccurateLanguages = "vision.reference_accurate_languages"
        case referenceAccuratePerform = "vision.reference_accurate_perform"
        case referenceAccurateCompletion = "vision.reference_accurate_completion"
        case referenceFastLanguages = "vision.reference_fast_languages"
        case referenceFastPerform = "vision.reference_fast_perform"
        case referenceFastCompletion = "vision.reference_fast_completion"
        case referenceRevision3Languages = "vision.reference_revision3_languages"
        case referenceRevision3Perform = "vision.reference_revision3_perform"
        case referenceRevision3Completion = "vision.reference_revision3_completion"
        case pdfSource = "pdf.source"
        case pdfPage = "pdf.page"
        case pdfRaster = "pdf.raster"
        case pdfRasterImage = "pdf.raster_image"
        case previewContactSheet = "preview.contact_sheet"
        case previewBitmap = "preview.bitmap"
        case previewJPEGDestination = "preview.jpeg_destination"
        case previewJPEGFinalize = "preview.jpeg_finalize"
        case videoAssetMetadata = "video.asset_metadata"
        case videoTrackMetadata = "video.track_metadata"
        case videoSample = "video.sample"
        case videoSampleTime = "video.sample_time"
    }

    enum ErrorKind: String {
        case cancellationError = "cancellation_error"
        case objcMissingError = "objc_missing_error"
        case other
    }

    enum ReferenceVariant: String {
        case modern, legacy, automatic

        var failurePhase: Phase {
            switch self {
            case .modern: .referenceModern
            case .legacy: .referenceLegacy
            case .automatic: .referenceAutomatic
            }
        }
    }

    enum ReferenceSourceFormat: String {
        case cgImage = "cg_image"
        case pngData = "png_data"
    }

    private static let allowedDomains: Set<String> = [
        VNErrorDomain, MLModelErrorDomain, AVFoundationErrorDomain,
        NSCocoaErrorDomain, NSPOSIXErrorDomain, NSOSStatusErrorDomain, NSURLErrorDomain,
    ]

    private static let missingObjCErrorTypes: Set<String> = [
        "Foundation._GenericObjCError", "FoundationEssentials._GenericObjCError", "Swift._GenericObjCError",
    ]

    static func errorKind(_ error: Error) -> ErrorKind {
        if error is CancellationError { return .cancellationError }
        // Compare only the concrete type against reserved, exact aliases. An
        // NSError or CustomNSError claiming the same domain is not evidence.
        if missingObjCErrorTypes.contains(String(reflecting: type(of: error))),
           (error as NSError).code == 0 { return .objcMissingError }
        return .other
    }

    static func payload(phase: Phase, error: Error, sourceFormat: ReferenceSourceFormat? = nil) -> Data? {
        var currentError: Error = error
        var errors: [[String: Any]] = []
        for depth in 0..<3 {
            let current = currentError as NSError
            errors.append(["domain": allowedDomains.contains(current.domain) ? current.domain : "other",
                           "code": current.code, "kind": errorKind(currentError).rawValue])
            guard depth < 2, let underlying = current.userInfo[NSUnderlyingErrorKey] as? Error else { break }
            currentError = underlying
        }
        var record: [String: Any] = ["format": "jarvis.native-media-diagnostic/1",
                                    "phase": phase.rawValue, "errors": errors]
        if let sourceFormat { record["source_format"] = sourceFormat.rawValue }
        return boundedJSON(record)
    }

    static func referencePayload(variant: ReferenceVariant, sourceFormat: ReferenceSourceFormat,
                                 recognizedMarker: Bool) -> Data? {
        boundedJSON(["format": "jarvis.native-media-reference/1", "variant": variant.rawValue,
                     "source_format": sourceFormat.rawValue,
                     "recognized_marker": recognizedMarker])
    }

    private static func boundedJSON(_ record: [String: Any]) -> Data? {
        guard var data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else { return nil }
        data.append(10)
        return data.count <= 1024 ? data : nil
    }

    static func enabled(environment: [String: String]) -> Bool {
        #if DEBUG
        if environment["GITHUB_ACTIONS"] == "true" { return true }
        guard environment["JARVIS_TEST_GUARD_PROFILE"] == "local",
              environment["JARVIS_TEST_GUARD_SELECTION"] == "NativeMedia(Executor|Diagnostics)Tests",
              environment["JARVIS_TEST_GUARD_NATIVE_MEDIA_FRAMEWORKS"] == "1",
              let nonce = environment["JARVIS_TEST_GUARD_NONCE"],
              environment["JARVIS_TEST_GUARD_CHILD_CHECKED"] == nonce,
              nonce.utf8.count == 48 else { return false }
        return nonce.utf8.allSatisfy { ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102) }
        #else
        return false
        #endif
    }

    static func emit(_ phase: Phase, error: Error, sourceFormat: ReferenceSourceFormat? = nil) {
        guard enabled(environment: ProcessInfo.processInfo.environment),
              let data = payload(phase: phase, error: error, sourceFormat: sourceFormat) else { return }
        try? FileHandle.standardError.write(contentsOf: data)
    }

    static func emitReferenceOutcome(_ variant: ReferenceVariant, sourceFormat: ReferenceSourceFormat,
                                     recognizedMarker: Bool) {
        guard enabled(environment: ProcessInfo.processInfo.environment),
              let data = referencePayload(variant: variant, sourceFormat: sourceFormat,
                                          recognizedMarker: recognizedMarker) else { return }
        try? FileHandle.standardError.write(contentsOf: data)
    }
}
