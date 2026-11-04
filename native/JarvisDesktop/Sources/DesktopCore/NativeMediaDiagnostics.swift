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
        case modern, legacy

        var failurePhase: Phase {
            switch self {
            case .modern: .referenceModern
            case .legacy: .referenceLegacy
            }
        }
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

    static func payload(phase: Phase, error: Error) -> Data? {
        var currentError: Error = error
        var errors: [[String: Any]] = []
        for depth in 0..<3 {
            let current = currentError as NSError
            errors.append(["domain": allowedDomains.contains(current.domain) ? current.domain : "other",
                           "code": current.code, "kind": errorKind(currentError).rawValue])
            guard depth < 2, let underlying = current.userInfo[NSUnderlyingErrorKey] as? Error else { break }
            currentError = underlying
        }
        let record: [String: Any] = ["format": "jarvis.native-media-diagnostic/1",
                                    "phase": phase.rawValue, "errors": errors]
        return boundedJSON(record)
    }

    static func referencePayload(variant: ReferenceVariant, recognizedMarker: Bool) -> Data? {
        boundedJSON(["format": "jarvis.native-media-reference/1", "variant": variant.rawValue,
                     "recognized_marker": recognizedMarker])
    }

    private static func boundedJSON(_ record: [String: Any]) -> Data? {
        guard var data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else { return nil }
        data.append(10)
        return data.count <= 1024 ? data : nil
    }

    static func enabled(environment: [String: String]) -> Bool {
        #if DEBUG
        return environment["GITHUB_ACTIONS"] == "true"
        #else
        return false
        #endif
    }

    static func emit(_ phase: Phase, error: Error) {
        guard enabled(environment: ProcessInfo.processInfo.environment),
              let data = payload(phase: phase, error: error) else { return }
        try? FileHandle.standardError.write(contentsOf: data)
    }

    static func emitReferenceOutcome(_ variant: ReferenceVariant, recognizedMarker: Bool) {
        guard enabled(environment: ProcessInfo.processInfo.environment),
              let data = referencePayload(variant: variant, recognizedMarker: recognizedMarker) else { return }
        try? FileHandle.standardError.write(contentsOf: data)
    }
}
