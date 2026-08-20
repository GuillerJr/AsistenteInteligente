import Foundation
import ImageIO
import UniformTypeIdentifiers

public enum LocalImageError: Error, Equatable {
    case invalidAttachment
    case unsafeSource
    case unsupportedSource
    case cannotFit
}

public struct LocalImageAttachment: Equatable, Sendable {
    public static let maximumBytes = 32_768

    public let mediaType: String
    public let data: Data

    public init(mediaType: String, data: Data) throws {
        let matchesSignature: Bool
        switch mediaType {
        case "image/jpeg":
            matchesSignature = data.starts(with: [0xFF, 0xD8, 0xFF])
        case "image/png":
            matchesSignature = data.starts(with: [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        case "image/webp":
            matchesSignature = data.count >= 12
                && data.starts(with: Data("RIFF".utf8))
                && data[8 ..< 12] == Data("WEBP".utf8)
        default:
            matchesSignature = false
        }
        guard !data.isEmpty, data.count <= Self.maximumBytes, matchesSignature else {
            throw LocalImageError.invalidAttachment
        }
        self.mediaType = mediaType
        self.data = data
    }
}

public enum LocalImageEncoder {
    private static let maximumSourceBytes = 20_000_000
    private static let maximumSourcePixels = 100_000_000
    private static let startingPixelSize = 1_024
    private static let minimumPixelSize = 128
    private static let qualities = [0.72, 0.52, 0.35, 0.22]

    public static func encodeFile(at url: URL) throws -> LocalImageAttachment {
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
        guard
            values.isRegularFile == true,
            let fileSize = values.fileSize,
            (1 ... maximumSourceBytes).contains(fileSize)
        else {
            throw LocalImageError.unsafeSource
        }
        guard
            let source = CGImageSourceCreateWithURL(
                url as CFURL,
                [kCGImageSourceShouldCache: false] as CFDictionary
            ),
            let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil)
                as? [CFString: Any],
            let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
            let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue,
            width > 0,
            height > 0,
            Int64(width) * Int64(height) <= Int64(maximumSourcePixels)
        else {
            throw LocalImageError.unsupportedSource
        }

        guard let image = CGImageSourceCreateThumbnailAtIndex(
            source,
            0,
            [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: startingPixelSize,
                kCGImageSourceShouldCacheImmediately: true,
            ] as CFDictionary
        ) else {
            throw LocalImageError.unsupportedSource
        }
        return try encodeImage(image)
    }

    public static func encodeImage(_ image: CGImage) throws -> LocalImageAttachment {
        guard
            image.width > 0,
            image.height > 0,
            Int64(image.width) * Int64(image.height) <= Int64(maximumSourcePixels)
        else {
            throw LocalImageError.unsupportedSource
        }

        var pixelSize = max(
            min(max(image.width, image.height), startingPixelSize),
            minimumPixelSize
        )
        while pixelSize >= minimumPixelSize {
            guard let scaled = scaledImage(image, maximumPixelSize: pixelSize) else {
                throw LocalImageError.unsupportedSource
            }
            for quality in qualities {
                if
                    let data = encodeJPEG(scaled, quality: quality),
                    data.count <= LocalImageAttachment.maximumBytes
                {
                    return try LocalImageAttachment(mediaType: "image/jpeg", data: data)
                }
            }
            if pixelSize == minimumPixelSize {
                break
            }
            pixelSize = max(minimumPixelSize, pixelSize / 2)
        }
        throw LocalImageError.cannotFit
    }

    private static func scaledImage(_ image: CGImage, maximumPixelSize: Int) -> CGImage? {
        let sourceMaximum = max(image.width, image.height)
        let scale = min(1, Double(maximumPixelSize) / Double(sourceMaximum))
        let width = max(1, Int((Double(image.width) * scale).rounded()))
        let height = max(1, Int((Double(image.height) * scale).rounded()))
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return nil
        }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage()
    }

    private static func encodeJPEG(_ image: CGImage, quality: Double) -> Data? {
        let output = NSMutableData()
        guard
            let destination = CGImageDestinationCreateWithData(
                output,
                UTType.jpeg.identifier as CFString,
                1,
                nil
            )
        else {
            return nil
        }
        CGImageDestinationAddImage(
            destination,
            image,
            [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
        )
        guard CGImageDestinationFinalize(destination) else {
            return nil
        }
        return output as Data
    }
}
