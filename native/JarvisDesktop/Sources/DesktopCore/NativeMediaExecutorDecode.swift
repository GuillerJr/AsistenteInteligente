import AppKit
import CryptoKit
import Foundation
import ImageIO
import PDFKit
import UniformTypeIdentifiers
import Vision

enum NativeMediaDecoder {
    static func inspect(_ request: NativeMediaRequest, data: Data,
                        cancellation: NativeMediaCancellation) async throws -> NativeMediaObservation {
        try cancellation.check(); try request.validate(data)
        switch request.kind {
        case "pdf": return try pdf(request, data: data, cancellation: cancellation)
        case "image": return try image(request, data: data, cancellation: cancellation)
        case "document": return try rtf(request, data: data, cancellation: cancellation)
        case "audio", "video": return try await audiovisual(request, data: data, cancellation: cancellation)
        default: throw NativeMediaIssue.unsupported
        }
    }

    static func image(_ request: NativeMediaRequest, data: Data,
                      cancellation: NativeMediaCancellation) throws -> NativeMediaObservation {
        guard let source = CGImageSourceCreateWithData(data as CFData,
                [kCGImageSourceShouldCache: false] as CFDictionary),
              let type = CGImageSourceGetType(source) else { throw NativeMediaIssue.decoderFailed }
        let actualMIME = UTType(type as String)?.preferredMIMEType
        guard actualMIME == request.mime || (request.mime == "image/heic" && actualMIME == "image/heif") else {
            throw NativeMediaIssue.unsupported
        }
        let count = CGImageSourceGetCount(source)
        guard (1...10000).contains(count),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
              let width = (properties[kCGImagePropertyPixelWidth as String] as? NSNumber)?.intValue,
              let height = (properties[kCGImagePropertyPixelHeight as String] as? NSNumber)?.intValue,
              width > 0, height > 0, width <= 40000000, height <= 40000000,
              Int64(width) * Int64(height) <= 40000000 else { throw NativeMediaIssue.limit }
        try cancellation.check()
        guard let picture = CGImageSourceCreateThumbnailAtIndex(source, 0, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: 1600,
            kCGImageSourceShouldCacheImmediately: true,
        ] as CFDictionary) else { throw NativeMediaIssue.decoderFailed }
        try cancellation.check()
        var result = NativeMediaObservation()
        try result.add("image:1", recognize(picture, locale: request.locale, cancellation: cancellation))
        result.metadata = ["dimensions": .object(["width": .number(Double(width)), "height": .number(Double(height))]),
                           "frame_count": .number(Double(count)), "observed_frame": .number(1),
                           "image_type": .string(type as String), "ocr_on_device": .bool(true),
                           "visual_sampling": .string("first_image_frame_thumbnail")]
        result.truncated = result.truncated || count > 1
        result.frame = try frame(picture, cancellation: cancellation)
        return result
    }

    static func pdf(_ request: NativeMediaRequest, data: Data,
                    cancellation: NativeMediaCancellation) throws -> NativeMediaObservation {
        guard data.starts(with: Data("%PDF-".utf8)), let document = PDFDocument(data: data) else {
            throw NativeMediaIssue.decoderFailed
        }
        guard !document.isEncrypted, !document.isLocked else { throw NativeMediaIssue.encryptedPDF }
        guard (1...64).contains(document.pageCount), request.pageStart <= document.pageCount else {
            throw NativeMediaIssue.limit
        }
        let final = min(document.pageCount, request.pageStart + request.pageCount - 1)
        var result = NativeMediaObservation(), pictures: [CGImage] = [], processed: [RPCValue] = []
        var scanned = 0
        for index in request.pageStart...final {
            try cancellation.check()
            guard let page = document.page(at: index - 1) else { throw NativeMediaIssue.decoderFailed }
            let picture = try render(page, maximum: 1400)
            pictures.append(picture); processed.append(.number(Double(index)))
            try cancellation.check()
            var text = page.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if text.isEmpty {
                scanned += 1
                text = try recognize(picture, locale: request.locale, cancellation: cancellation)
            }
            try result.add("page:\(index)", text)
        }
        try cancellation.check()
        result.metadata = ["page_count": .number(Double(document.pageCount)),
                           "processed_pages": .array(processed), "page_selection": .string("explicit"),
                           "ocr_page_count": .number(Double(scanned)), "ocr_on_device": .bool(true),
                           "visual_sampling": .string("contact_sheet_of_selected_pages"),
                           "external_links_opened": .bool(false)]
        result.truncated = result.truncated || pictures.count < document.pageCount
        result.frame = try frame(contactSheet(pictures, columns: pictures.count == 1 ? 1 : 2),
                                 cancellation: cancellation)
        return result
    }

    static func rtf(_ request: NativeMediaRequest, data: Data,
                    cancellation: NativeMediaCancellation) throws -> NativeMediaObservation {
        guard request.mime == "application/rtf", data.starts(with: Data("{\\rtf".utf8)) else {
            throw NativeMediaIssue.unsupported
        }
        let lexical = String(decoding: data, as: UTF8.self).lowercased()
        let blocked = ["\\object", "\\objdata", "\\filetbl", "\\objlink", "\\field", "\\fromhtml", "\\htmltag",
                       "\\pict", "\\bin", "\\datastore", "\\xmlopen", "\\htmlbase"]
        guard !blocked.contains(where: lexical.contains) else { throw NativeMediaIssue.unsupported }
        try cancellation.check()
        let text: String
        do {
            text = try NSAttributedString(data: data, options: [.documentType: NSAttributedString.DocumentType.rtf],
                                          documentAttributes: nil).string
        } catch { throw NativeMediaIssue.decoderFailed }
        try cancellation.check()
        var result = NativeMediaObservation()
        try result.add("page:1", text)
        result.metadata = ["page_numbering": .string("logical_plain_text_document"),
                           "external_links_opened": .bool(false), "embedded_objects_executed": .bool(false)]
        return result
    }

    static func recognize(_ image: CGImage, locale: String,
                          cancellation: NativeMediaCancellation) throws -> String {
        try cancellation.check()
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let languages: [String]
        do { languages = try request.supportedRecognitionLanguages() }
        catch { throw NativeMediaIssue.localeUnavailable }
        guard let selected = languages.first(where: { LocalSpeechPolicy.canonicalLocale($0) == locale }) else {
            throw NativeMediaIssue.localeUnavailable
        }
        request.recognitionLanguages = [selected]
        let registration = cancellation.register { request.cancel() }
        defer { cancellation.remove(registration) }
        try cancellation.check()
        do { try VNImageRequestHandler(cgImage: image, options: [:]).perform([request]) }
        catch { try cancellation.check(); throw NativeMediaIssue.decoderFailed }
        try cancellation.check()
        // Reject an oversized OCR result rather than presenting a hidden prefix
        // as complete observation of the selected image or page.
        var text = "", bytes = 0
        for observation in request.results ?? [] {
            try cancellation.check()
            guard let candidate = observation.topCandidates(1).first else { continue }
            let line = candidate.string
            guard line.utf8.count <= 49152 else { throw NativeMediaIssue.limit }
            bytes += line.utf8.count + 1
            guard bytes <= 49152 else { throw NativeMediaIssue.limit }
            if !text.isEmpty { text += "\n" }
            text += line
        }
        return text
    }

    static func render(_ page: PDFPage, maximum: Int) throws -> CGImage {
        let box = page.bounds(for: .cropBox)
        guard box.origin.x.isFinite, box.origin.y.isFinite, box.width.isFinite, box.height.isFinite,
              box.width > 0, box.height > 0, box.width <= 20000, box.height <= 20000,
              box.width * box.height <= 40000000 else { throw NativeMediaIssue.limit }
        let scale = min(Double(maximum) / box.width, Double(maximum) / box.height)
        let width = max(1, Int(ceil(box.width * scale))), height = max(1, Int(ceil(box.height * scale)))
        guard let context = bitmap(width: width, height: height) else { throw NativeMediaIssue.decoderFailed }
        context.setFillColor(CGColor(gray: 1, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.scaleBy(x: scale, y: scale); context.translateBy(x: -box.minX, y: -box.minY)
        page.draw(with: .cropBox, to: context)
        guard let image = context.makeImage() else { throw NativeMediaIssue.decoderFailed }
        return image
    }

    static func bitmap(width: Int, height: Int) -> CGContext? {
        guard width > 0, height > 0, width <= 4096, height <= 4096,
              width * height <= 4000000 else { return nil }
        return CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                         bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                         bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    }

    static func contactSheet(_ pictures: [CGImage], columns: Int) throws -> CGImage {
        guard !pictures.isEmpty, pictures.count <= 8, (1...3).contains(columns) else { throw NativeMediaIssue.limit }
        let cellWidth = pictures.count == 1 ? 1000 : 500, cellHeight = pictures.count == 1 ? 1000 : 500
        let rows = (pictures.count + columns - 1) / columns
        guard let context = bitmap(width: cellWidth * columns, height: cellHeight * rows) else {
            throw NativeMediaIssue.limit
        }
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: cellWidth * columns, height: cellHeight * rows))
        for (index, picture) in pictures.enumerated() {
            let ratio = min(Double(cellWidth) / Double(picture.width), Double(cellHeight) / Double(picture.height))
            let width = Double(picture.width) * ratio, height = Double(picture.height) * ratio
            let x = Double(index % columns * cellWidth) + (Double(cellWidth) - width) / 2
            let y = Double((rows - 1 - index / columns) * cellHeight) + (Double(cellHeight) - height) / 2
            context.draw(picture, in: CGRect(x: x, y: y, width: width, height: height))
        }
        guard let result = context.makeImage() else { throw NativeMediaIssue.decoderFailed }
        return result
    }

    static func frame(_ source: CGImage, cancellation: NativeMediaCancellation) throws -> RPCValue {
        var image = source
        for reduction in 0..<4 {
            try cancellation.check()
            if reduction > 0 {
                let width = max(1, image.width * 2 / 3), height = max(1, image.height * 2 / 3)
                guard let context = bitmap(width: width, height: height) else { throw NativeMediaIssue.limit }
                context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
                guard let reduced = context.makeImage() else { throw NativeMediaIssue.decoderFailed }
                image = reduced
            }
            for quality in [0.65, 0.4, 0.2] {
                try cancellation.check()
                let target = NSMutableData()
                guard let destination = CGImageDestinationCreateWithData(target as CFMutableData,
                        UTType.jpeg.identifier as CFString, 1, nil) else { throw NativeMediaIssue.decoderFailed }
                CGImageDestinationAddImage(destination, image,
                    [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
                guard CGImageDestinationFinalize(destination) else { throw NativeMediaIssue.decoderFailed }
                let bytes = target as Data
                if bytes.count <= 49152 {
                    let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
                    return .object(["data_url": .string("data:image/jpeg;base64," + bytes.base64EncodedString()),
                                    "bytes": .number(Double(bytes.count)), "sha256": .string(digest),
                                    "storage": .string("memory_only")])
                }
            }
        }
        throw NativeMediaIssue.limit
    }
}
