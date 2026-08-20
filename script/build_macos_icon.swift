#!/usr/bin/env swift

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum IconBuildError: Error {
    case invalidArguments
    case invalidSource
    case contextUnavailable
    case imageUnavailable
    case destinationUnavailable
    case finalizeFailed
    case invalidOutput
}

guard CommandLine.arguments.count == 3 else {
    fputs("usage: build_macos_icon.swift <source.png> <output.icns>\n", stderr)
    throw IconBuildError.invalidArguments
}

let sourceURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
guard
    sourceURL.pathExtension.lowercased() == "png",
    outputURL.pathExtension.lowercased() == "icns",
    let source = CGImageSourceCreateWithURL(sourceURL as CFURL, nil),
    CGImageSourceGetCount(source) == 1,
    let sourceImage = CGImageSourceCreateImageAtIndex(source, 0, nil),
    sourceImage.width == sourceImage.height,
    sourceImage.width >= 1024
else {
    throw IconBuildError.invalidSource
}

let sizes = [16, 32, 64, 128, 256, 512, 1024]
guard let destination = CGImageDestinationCreateWithURL(
    outputURL as CFURL,
    UTType.icns.identifier as CFString,
    sizes.count,
    nil
) else {
    throw IconBuildError.destinationUnavailable
}

for size in sizes {
    guard let context = CGContext(
        data: nil,
        width: size,
        height: size,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        throw IconBuildError.contextUnavailable
    }
    context.interpolationQuality = .high
    context.draw(sourceImage, in: CGRect(x: 0, y: 0, width: size, height: size))
    guard let image = context.makeImage() else {
        throw IconBuildError.imageUnavailable
    }
    CGImageDestinationAddImage(destination, image, nil)
}

guard CGImageDestinationFinalize(destination) else {
    throw IconBuildError.finalizeFailed
}
guard
    let output = CGImageSourceCreateWithURL(outputURL as CFURL, nil),
    CGImageSourceGetType(output) as String? == UTType.icns.identifier,
    CGImageSourceGetCount(output) >= 4
else {
    throw IconBuildError.invalidOutput
}

print("status=ok representations=\(CGImageSourceGetCount(output))")
