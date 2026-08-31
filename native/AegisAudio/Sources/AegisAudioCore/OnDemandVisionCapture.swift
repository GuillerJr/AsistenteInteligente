@preconcurrency import CoreGraphics
import CoreImage
import CryptoKit
import Foundation
import ImageIO
@preconcurrency import ScreenCaptureKit
import UniformTypeIdentifiers

public struct LocalizedVisionCapture: Sendable, Equatable {
    public let windowID: CGWindowID
    public let processIdentifier: pid_t
    public let cropInWindow: CGRect
    public let expectedPointInCrop: CGPoint
    public let imageData: Data
    public let imageSHA256: String

    public init(
        windowID: CGWindowID,
        processIdentifier: pid_t,
        cropInWindow: CGRect,
        expectedPointInCrop: CGPoint,
        imageData: Data,
        imageSHA256: String
    ) {
        self.windowID = windowID
        self.processIdentifier = processIdentifier
        self.cropInWindow = cropInWindow
        self.expectedPointInCrop = expectedPointInCrop
        self.imageData = imageData
        self.imageSHA256 = imageSHA256
    }
}

public struct LocalizedVisionDecision: Codable, Sendable, Equatable {
    public let offsetX: Double
    public let offsetY: Double
    public let confidence: Double
    public let targetFound: Bool

    enum CodingKeys: String, CodingKey {
        case offsetX = "offset_x"
        case offsetY = "offset_y"
        case confidence
        case targetFound = "target_found"
    }

    public init(offsetX: Double, offsetY: Double, confidence: Double, targetFound: Bool) {
        self.offsetX = offsetX
        self.offsetY = offsetY
        self.confidence = confidence
        self.targetFound = targetFound
    }

    public func validatedPoint(around expectedPoint: CGPoint) throws -> CGPoint {
        guard
            targetFound,
            offsetX.isFinite,
            offsetY.isFinite,
            confidence.isFinite,
            (-60.0 ... 60.0).contains(offsetX),
            (-60.0 ... 60.0).contains(offsetY),
            (0.75 ... 1.0).contains(confidence)
        else {
            throw OnDemandVisionCaptureError.untrustedDecision
        }
        return CGPoint(x: expectedPoint.x + offsetX, y: expectedPoint.y + offsetY)
    }
}

public enum OnDemandVisionCaptureError: Error, Sendable, Equatable {
    case invalidRequest
    case screenRecordingDenied
    case windowNotFound
    case windowOwnershipMismatch
    case expectedPointOutsideWindow
    case captureFailed
    case encodingFailed
    case untrustedDecision
}

public enum OnDemandVisionCapture {
    public static let edgeLength = 120
    public static let maximumEncodedBytes = 128 * 1_024

    public static func capture(
        windowID: CGWindowID,
        processIdentifier: pid_t,
        expectedScreenPoint: CGPoint
    ) async throws -> LocalizedVisionCapture {
        guard
            windowID > 0,
            processIdentifier > 0,
            expectedScreenPoint.x.isFinite,
            expectedScreenPoint.y.isFinite
        else {
            throw OnDemandVisionCaptureError.invalidRequest
        }
        guard CGPreflightScreenCaptureAccess() else {
            throw OnDemandVisionCaptureError.screenRecordingDenied
        }
        let description = try windowDescription(
            windowID: windowID,
            processIdentifier: processIdentifier
        )
        guard description.bounds.contains(expectedScreenPoint) else {
            throw OnDemandVisionCaptureError.expectedPointOutsideWindow
        }

        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: true
            )
        } catch {
            throw OnDemandVisionCaptureError.screenRecordingDenied
        }
        guard let window = content.windows.first(where: {
            $0.windowID == windowID && $0.owningApplication?.processID == processIdentifier
        }) else {
            throw OnDemandVisionCaptureError.windowNotFound
        }

        let side = CGFloat(edgeLength)
        let pointInWindow = CGPoint(
            x: expectedScreenPoint.x - description.bounds.minX,
            y: expectedScreenPoint.y - description.bounds.minY
        )
        let crop = clampedCrop(
            centeredAt: pointInWindow,
            size: CGSize(width: side, height: side),
            within: CGRect(origin: .zero, size: description.bounds.size)
        )
        let configuration = SCStreamConfiguration()
        configuration.sourceRect = crop
        configuration.width = edgeLength
        configuration.height = edgeLength
        configuration.showsCursor = false
        configuration.capturesAudio = false
        configuration.ignoreShadowsSingleWindow = true
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let image: CGImage
        do {
            image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
        } catch {
            throw OnDemandVisionCaptureError.captureFailed
        }
        guard image.width == edgeLength, image.height == edgeLength else {
            throw OnDemandVisionCaptureError.captureFailed
        }
        let encoded = try encodePNG(image)
        guard !encoded.isEmpty, encoded.count <= maximumEncodedBytes else {
            throw OnDemandVisionCaptureError.encodingFailed
        }
        let relativeExpected = CGPoint(
            x: pointInWindow.x - crop.minX,
            y: pointInWindow.y - crop.minY
        )
        return LocalizedVisionCapture(
            windowID: windowID,
            processIdentifier: processIdentifier,
            cropInWindow: crop,
            expectedPointInCrop: relativeExpected,
            imageData: encoded,
            imageSHA256: SHA256.hash(data: encoded).map {
                String(format: "%02x", $0)
            }.joined()
        )
    }

    private static func windowDescription(
        windowID: CGWindowID,
        processIdentifier: pid_t
    ) throws -> (bounds: CGRect, ownerPID: pid_t) {
        let identifiers = [NSNumber(value: windowID)] as CFArray
        guard
            let descriptions = CGWindowListCreateDescriptionFromArray(identifiers)
                as? [[CFString: Any]],
            let description = descriptions.first,
            let owner = description[kCGWindowOwnerPID] as? NSNumber,
            let rawBounds = description[kCGWindowBounds] as? NSDictionary,
            let bounds = CGRect(dictionaryRepresentation: rawBounds),
            bounds.width >= 1,
            bounds.height >= 1
        else {
            throw OnDemandVisionCaptureError.windowNotFound
        }
        guard owner.int32Value == processIdentifier else {
            throw OnDemandVisionCaptureError.windowOwnershipMismatch
        }
        return (bounds.standardized, owner.int32Value)
    }

    private static func clampedCrop(
        centeredAt point: CGPoint,
        size: CGSize,
        within bounds: CGRect
    ) -> CGRect {
        let width = min(size.width, bounds.width)
        let height = min(size.height, bounds.height)
        let x = min(max(point.x - width / 2, bounds.minX), bounds.maxX - width)
        let y = min(max(point.y - height / 2, bounds.minY), bounds.maxY - height)
        return CGRect(x: x, y: y, width: width, height: height).integral
    }

    private static func encodePNG(_ image: CGImage) throws -> Data {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            data,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            throw OnDemandVisionCaptureError.encodingFailed
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw OnDemandVisionCaptureError.encodingFailed
        }
        return data as Data
    }
}
