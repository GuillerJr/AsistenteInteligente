import CoreGraphics
import Foundation
import ImageIO
import Testing
import UniformTypeIdentifiers
@testable import AegisAudioCore

@Test func localImageAttachmentRequiresBoundedMatchingSignature() throws {
    let jpeg = try LocalImageAttachment(
        mediaType: "image/jpeg",
        data: Data([0xFF, 0xD8, 0xFF, 0xD9])
    )
    #expect(jpeg.data.count == 4)

    #expect(throws: LocalImageError.invalidAttachment) {
        try LocalImageAttachment(mediaType: "image/png", data: jpeg.data)
    }
    #expect(throws: LocalImageError.invalidAttachment) {
        try LocalImageAttachment(
            mediaType: "image/jpeg",
            data: Data(repeating: 0xFF, count: LocalImageAttachment.maximumBytes + 1)
        )
    }
}

@Test func localImageEncoderProducesBoundedJPEG() throws {
    let source = FileManager.default.temporaryDirectory
        .appendingPathComponent("aegis-image-\(UUID().uuidString).png")
    defer { try? FileManager.default.removeItem(at: source) }
    try makePNG(width: 1_600, height: 900).write(to: source, options: .atomic)

    let attachment = try LocalImageEncoder.encodeFile(at: source)

    #expect(attachment.mediaType == "image/jpeg")
    #expect(attachment.data.count <= LocalImageAttachment.maximumBytes)
    #expect(attachment.data.starts(with: [0xFF, 0xD8, 0xFF]))
}

@Test func localImageEncoderRejectsDirectory() {
    #expect(throws: LocalImageError.unsafeSource) {
        try LocalImageEncoder.encodeFile(at: FileManager.default.temporaryDirectory)
    }
}

private func makePNG(width: Int, height: Int) throws -> Data {
    let context = try #require(
        CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )
    )
    context.setFillColor(CGColor(red: 0.08, green: 0.35, blue: 0.78, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    let image = try #require(context.makeImage())
    let output = NSMutableData()
    let destination = try #require(
        CGImageDestinationCreateWithData(
            output,
            UTType.png.identifier as CFString,
            1,
            nil
        )
    )
    CGImageDestinationAddImage(destination, image, nil)
    #expect(CGImageDestinationFinalize(destination))
    return output as Data
}
