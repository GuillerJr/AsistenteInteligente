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

    private static let allowedDomains: Set<String> = [
        VNErrorDomain, MLModelErrorDomain, AVFoundationErrorDomain,
        NSCocoaErrorDomain, NSPOSIXErrorDomain, NSOSStatusErrorDomain, NSURLErrorDomain,
    ]

    static func payload(phase: Phase, error: Error) -> Data? {
        var current = error as NSError
        var errors: [[String: Any]] = []
        for depth in 0..<3 {
            errors.append(["domain": allowedDomains.contains(current.domain) ? current.domain : "other",
                           "code": current.code])
            guard depth < 2, let underlying = current.userInfo[NSUnderlyingErrorKey] as? NSError else { break }
            current = underlying
        }
        let record: [String: Any] = ["format": "jarvis.native-media-diagnostic/1",
                                    "phase": phase.rawValue, "errors": errors]
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
}
