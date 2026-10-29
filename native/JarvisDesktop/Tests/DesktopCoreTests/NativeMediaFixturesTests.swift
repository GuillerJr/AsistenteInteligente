@preconcurrency import AVFoundation
import AppKit
import CoreGraphics
import CoreText
import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import DesktopCore

/// Generated only by remote CI. No owner file, microphone, account or network
/// is used; the sole disk fixture is a tiny video removed by its creator.
enum NativeMediaFixtures {
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func identifier() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
    static func args(_ data: Data, kind: String = "pdf", mime: String = "application/pdf",
                     name: String = "prueba.pdf", action: String = "extract",
                     start: Int = 1, count: Int = 8, locale: String = "en-US") -> [String: RPCValue] {
        ["operation_id": .string(identifier()), "attachment_id": .string(identifier()),
         "attachment_sha256": .string(digest(data)), "source_sha256": .string(digest(data)),
         "payload_sha256": .string(digest(data)), "byte_length": .number(Double(data.count)),
         "name": .string(name), "mime": .string(mime), "kind": .string(kind), "action": .string(action),
         "page_start": .number(Double(start)), "page_count": .number(Double(count)), "locale": .string(locale)]
    }
    static func picture(_ marker: String) throws -> CGImage {
        let context = try XCTUnwrap(NativeMediaDecoder.bitmap(width: 1200, height: 300))
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 1200, height: 300))
        draw(marker, in: context, position: CGPoint(x: 50, y: 130))
        return try XCTUnwrap(context.makeImage())
    }
    static func draw(_ marker: String, in context: CGContext, position: CGPoint) {
        let text = NSAttributedString(string: marker, attributes: [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 44, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1),
        ])
        context.textMatrix = .identity; context.textPosition = position
        CTLineDraw(CTLineCreateWithAttributedString(text), context)
    }
    static func png(_ marker: String = "JARVIS LOCAL IMAGE 4826") throws -> Data {
        let data = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(data as CFMutableData,
            UTType.png.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(destination, try picture(marker), nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }
    static func pdf(_ markers: [String], scanned: Bool = false, encrypted: Bool = false) throws -> Data {
        let data = NSMutableData(), consumer = try XCTUnwrap(CGDataConsumer(data: data as CFMutableData))
        var box = CGRect(x: 0, y: 0, width: 600, height: 300)
        let options: CFDictionary? = encrypted ? [
            kCGPDFContextUserPassword: "synthetic-user-only", kCGPDFContextOwnerPassword: "synthetic-owner-only",
        ] as CFDictionary : nil
        let context = try XCTUnwrap(CGContext(consumer: consumer, mediaBox: &box, options))
        for marker in markers {
            context.beginPDFPage(nil)
            if scanned { context.draw(try picture(marker), in: box) }
            else { draw(marker, in: context, position: CGPoint(x: 20, y: 140)) }
            context.endPDFPage()
        }
        context.closePDF(); return data as Data
    }
    static func wav(seconds: Int = 1) -> Data {
        let samples = 8000 * seconds, length = samples * 2
        var data = Data("RIFF".utf8)
        append(UInt32(36 + length), to: &data); data.append(Data("WAVEfmt ".utf8))
        append(UInt32(16), to: &data); append(UInt16(1), to: &data); append(UInt16(1), to: &data)
        append(UInt32(8000), to: &data); append(UInt32(16000), to: &data)
        append(UInt16(2), to: &data); append(UInt16(16), to: &data)
        data.append(Data("data".utf8)); append(UInt32(length), to: &data)
        data.append(Data(repeating: 0, count: length)); return data
    }
    private static func append<T: FixedWidthInteger>(_ number: T, to data: inout Data) {
        var little = number.littleEndian
        withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
    }
    static func video() async throws -> Data {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-media-video-" + identifier())
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("fixture.mp4")
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: 64, AVVideoHeightKey: 64,
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input,
            sourcePixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
                                         kCVPixelBufferWidthKey as String: 64, kCVPixelBufferHeightKey as String: 64])
        guard writer.canAdd(input) else { throw NativeMediaIssue.decoderFailed }
        writer.add(input)
        guard writer.startWriting() else { throw writer.error ?? NativeMediaIssue.decoderFailed }
        writer.startSession(atSourceTime: .zero)
        do {
            for index in 0..<3 {
                var attempts = 0
                while !input.isReadyForMoreMediaData, attempts < 100 {
                    try await Task.sleep(for: .milliseconds(10)); attempts += 1
                }
                guard input.isReadyForMoreMediaData else { throw NativeMediaIssue.deadline }
                var output: CVPixelBuffer?
                guard CVPixelBufferCreate(kCFAllocatorDefault, 64, 64, kCVPixelFormatType_32ARGB, nil, &output) == kCVReturnSuccess,
                      let buffer = output else { throw NativeMediaIssue.decoderFailed }
                CVPixelBufferLockBaseAddress(buffer, [])
                if let base = CVPixelBufferGetBaseAddress(buffer) {
                    memset(base, Int32(220 - index * 30), CVPixelBufferGetBytesPerRow(buffer) * 64)
                }
                CVPixelBufferUnlockBaseAddress(buffer, [])
                guard adaptor.append(buffer, withPresentationTime: CMTime(value: Int64(index), timescale: 2)) else {
                    throw writer.error ?? NativeMediaIssue.decoderFailed
                }
            }
            input.markAsFinished(); writer.endSession(atSourceTime: CMTime(value: 3, timescale: 2))
            await writer.finishWriting()
            guard writer.status == .completed else { throw writer.error ?? NativeMediaIssue.decoderFailed }
            return try Data(contentsOf: url)
        } catch { writer.cancelWriting(); throw error }
    }
}
