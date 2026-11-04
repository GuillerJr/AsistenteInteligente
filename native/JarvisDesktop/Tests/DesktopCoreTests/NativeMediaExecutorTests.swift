@preconcurrency import AVFoundation
import CoreGraphics
import CryptoKit
import Foundation
import ImageIO
import XCTest
@testable import DesktopCore

final class NativeMediaExecutorTests: XCTestCase {
    func testMemoryAssetForbidsEveryExternalReferenceBeforeLoading() throws {
        let source = try MemoryMediaAsset(data: NativeMediaFixtures.wav(), mimeType: "audio/wav")
        defer { source.close() }
        XCTAssertEqual(source.asset.referenceRestrictions, .forbidAll)
        XCTAssertEqual(source.asset.url.scheme, "jarvis-media")
        XCTAssertFalse(source.asset.url.isFileURL)
    }
    func testVideoPresentationTransformCannotConcealOversizedEncodedRaster() throws {
        XCTAssertThrowsError(try NativeMediaDecoder.checkedVideoDimensions(CGSize(width: 10000, height: 10000),
            transform: CGAffineTransform(scaleX: 0.1, y: 0.1))) {
            XCTAssertEqual($0 as? NativeMediaIssue, .limit)
        }
        XCTAssertThrowsError(try NativeMediaDecoder.checkedVideoDimensions(CGSize(width: 640, height: 480),
            transform: CGAffineTransform(scaleX: .infinity, y: 1)))
        XCTAssertThrowsError(try NativeMediaDecoder.checkedVideoDimensions(CGSize(width: 1000, height: 1000),
            transform: CGAffineTransform(a: 1000, b: 0, c: -999, d: 1, tx: 0, ty: 0)))
        let rotation = CGAffineTransform(a: 0, b: 1, c: -1, d: 0, tx: 480, ty: 0)
        XCTAssertEqual(try NativeMediaDecoder.checkedVideoDimensions(CGSize(width: 640, height: 480), transform: rotation),
                       CGSize(width: 480, height: 640))
    }
    @MainActor private func executor(_ payload: Data) -> NativeMediaExecutor {
        let result = NativeMediaExecutor()
        result.beforeEffect = { _ in }; result.loadPayload = { _ in payload }
        return result
    }
    @MainActor private func issue(_ operation: () async throws -> RPCValue) async -> NativeMediaIssue? {
        do { _ = try await operation(); XCTFail("Se esperaba un rechazo local"); return nil }
        catch { return error as? NativeMediaIssue }
    }
    @discardableResult
    private func assertFrame(_ result: RPCValue, file: StaticString = #filePath, line: UInt = #line) throws -> CGImage {
        let frame = result["frame"]
        XCTAssertEqual(frame["storage"], .string("memory_only"), file: file, line: line)
        let url = try XCTUnwrap(frame["data_url"].string, file: file, line: line)
        XCTAssertTrue(url.hasPrefix("data:image/jpeg;base64,"), file: file, line: line)
        let bytes = try XCTUnwrap(Data(base64Encoded: String(url.dropFirst(23))), file: file, line: line)
        XCTAssertLessThanOrEqual(bytes.count, 49152, file: file, line: line)
        XCTAssertEqual(frame["bytes"].integer, bytes.count, file: file, line: line)
        XCTAssertEqual(frame["sha256"].string, NativeMediaFixtures.digest(bytes), file: file, line: line)
        XCTAssertNil(result["metadata"]["data_url"].string, file: file, line: line)
        let source = try XCTUnwrap(CGImageSourceCreateWithData(bytes as CFData, nil), file: file, line: line)
        let picture = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil), file: file, line: line)
        XCTAssertGreaterThan(picture.width, 0, file: file, line: line)
        XCTAssertGreaterThan(picture.height, 0, file: file, line: line)
        return picture
    }
    private func assertRenderedInk(_ picture: CGImage) throws {
        let context = try XCTUnwrap(NativeMediaDecoder.bitmap(width: 64, height: 64))
        context.draw(picture, in: CGRect(x: 0, y: 0, width: 64, height: 64))
        let pixels = try XCTUnwrap(context.data?.assumingMemoryBound(to: UInt8.self))
        let samples = stride(from: 0, to: 64 * 64 * 4, by: 4).map { Int(pixels[$0]) }
        XCTAssertLessThan(try XCTUnwrap(samples.min()), 235, "El frame debe contener los píxeles dibujados")
        XCTAssertGreaterThan(try XCTUnwrap(samples.max()), 245)
    }
    private func assertVisual(_ result: RPCValue, locators: [String], columns: Int,
                              localTextExtraction: String) throws {
        let metadata = result["metadata"], rows = (locators.count + columns - 1) / columns
        XCTAssertEqual(metadata["extraction_policy"], .string("embedded_text_and_visual_v1"))
        XCTAssertEqual(metadata["local_text_extraction"], .string(localTextExtraction))
        XCTAssertEqual(metadata["ocr_performed"], .bool(false))
        XCTAssertEqual(metadata["visual_analysis"], .string("model_required"))
        XCTAssertEqual(metadata["visual_required"], .bool(true))
        XCTAssertEqual(metadata["visual_locators"], .array(locators.map(RPCValue.string)))
        XCTAssertEqual(metadata["visual_layout"], .string("row_major_top_left"))
        XCTAssertEqual(metadata["visual_columns"], .number(Double(columns)))
        XCTAssertEqual(metadata["visual_rows"], .number(Double(rows)))
        let regions = try XCTUnwrap(metadata["visual_regions"].array)
        XCTAssertEqual(regions.count, locators.count)
        for (index, region) in regions.enumerated() {
            let row = index / columns, column = index % columns
            XCTAssertEqual(region["locator"], .string(locators[index]))
            XCTAssertEqual(region["row"], .number(Double(row)))
            XCTAssertEqual(region["column"], .number(Double(column)))
            XCTAssertEqual(region["x"], .number(Double(column) / Double(columns)))
            XCTAssertEqual(region["y"], .number(Double(row) / Double(rows)))
            XCTAssertEqual(region["width"], .number(1 / Double(columns)))
            XCTAssertEqual(region["height"], .number(1 / Double(rows)))
        }
    }

    @MainActor func testPDFExactPageSelectionHasCitationsAndExplicitPartialObservation() async throws {
        let payload = try NativeMediaFixtures.pdf(["PAGE ONE 4826", "PAGE TWO 7319"])
        let host = executor(payload)
        let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, start: 2, count: 1))
        XCTAssertEqual(result["schema"], .string("jarvis.media.v1")); XCTAssertEqual(result["verified"], .bool(true))
        XCTAssertEqual(result["source_sha256"].string, NativeMediaFixtures.digest(payload))
        XCTAssertEqual(result["segments"].array?.count, 1)
        XCTAssertEqual(result["segments"].array?.first?["locator"], .string("page:2"))
        XCTAssertTrue(result["segments"].array?.first?["text"].string?.contains("7319") == true)
        XCTAssertFalse(result["segments"].array?.first?["text"].string?.contains("4826") == true)
        XCTAssertEqual(result["truncated"], .bool(true))
        XCTAssertEqual(result["metadata"]["processed_pages"], .array([.number(2)]))
        XCTAssertEqual(result["metadata"]["external_links_opened"], .bool(false))
        XCTAssertEqual(result["metadata"]["pages_without_embedded_text"], .array([]))
        XCTAssertEqual(result["metadata"]["embedded_text_page_count"], .number(1))
        try assertVisual(result, locators: ["page:2"], columns: 1, localTextExtraction: "pdf_embedded_text")
        try assertFrame(result)
    }
    @MainActor func testScannedPDFAndPNGPrepareRealFramesWithoutClaimingOCR() async throws {
        for (payload, kind, mime, name, locator) in [
            (try NativeMediaFixtures.png(), "image", "image/png", "prueba.png", "image:1"),
            (try NativeMediaFixtures.pdf(["JARVIS LOCAL IMAGE 4826"], scanned: true), "pdf", "application/pdf", "scan.pdf", "page:1"),
        ] {
            let host = executor(payload)
            let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: kind, mime: mime, name: name))
            XCTAssertEqual(result["on_device"], .bool(true))
            XCTAssertEqual(result["source_sha256"].string, NativeMediaFixtures.digest(payload))
            XCTAssertEqual(result["segments"].array?.first?["locator"], .string(locator))
            XCTAssertEqual(result["segments"].array?.first?["text"], .string(""))
            XCTAssertEqual(result["metadata"]["ocr_on_device"], .null)
            try assertVisual(result, locators: [locator], columns: 1,
                             localTextExtraction: kind == "pdf" ? "pdf_embedded_text" : "none")
            if kind == "pdf" {
                XCTAssertEqual(result["metadata"]["pages_without_embedded_text"], .array([.number(1)]))
                XCTAssertEqual(result["metadata"]["embedded_text_page_count"], .number(0))
            }
            try assertRenderedInk(assertFrame(result))
        }
    }
    @MainActor func testPDFContactSheetMapsEveryEmbeddedPageInRowMajorOrder() async throws {
        let payload = try NativeMediaFixtures.pdf(["PAGE ONE", "PAGE TWO", "PAGE THREE"])
        let result = try await executor(payload).execute("media.inspect", arguments: NativeMediaFixtures.args(payload))
        XCTAssertEqual(result["segments"].array?.map { $0["locator"] }, [
            .string("page:1"), .string("page:2"), .string("page:3")])
        for (index, segment) in (result["segments"].array ?? []).enumerated() {
            XCTAssertTrue(segment["text"].string?.contains(["ONE", "TWO", "THREE"][index]) == true)
        }
        XCTAssertEqual(result["truncated"], .bool(false))
        try assertVisual(result, locators: ["page:1", "page:2", "page:3"], columns: 2,
                         localTextExtraction: "pdf_embedded_text")
        try assertRenderedInk(assertFrame(result))
    }
    @MainActor func testExtractionPolicyIsRequiredBeforeAuthorityOrLoading() async throws {
        let payload = Data("{\\rtf1 POLICY}".utf8), host = NativeMediaExecutor()
        var checks = 0, loads = 0
        host.beforeEffect = { _ in checks += 1 }
        host.loadPayload = { _ in loads += 1; return payload }
        let policies: [RPCValue?] = [nil, .string("legacy_ocr"), .bool(true)]
        for policy in policies {
            var args = NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "policy.rtf")
            args["extraction_policy"] = policy
            let error = await issue { try await host.execute("media.inspect", arguments: args) }
            XCTAssertEqual(error, .invalidArguments)
        }
        XCTAssertEqual(checks, 0); XCTAssertEqual(loads, 0)
    }
    @MainActor func testDecodedJPEGContactSheetColorsMatchTopLeftRowMajorCitations() async throws {
        let colors = [CGColor(red: 1, green: 0, blue: 0, alpha: 1),
                      CGColor(red: 0, green: 1, blue: 0, alpha: 1),
                      CGColor(red: 0, green: 0, blue: 1, alpha: 1),
                      CGColor(red: 1, green: 1, blue: 0, alpha: 1)]
        let payload = try NativeMediaFixtures.colorPDF(colors)
        let result = try await executor(payload).execute("media.inspect", arguments: NativeMediaFixtures.args(payload))
        try assertVisual(result, locators: ["page:1", "page:2", "page:3", "page:4"], columns: 2,
                         localTextExtraction: "pdf_embedded_text")
        XCTAssertTrue(result["segments"].array?.allSatisfy { $0["text"] == .string("") } == true)
        let picture = try assertFrame(result)
        let rgba = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
        let raster = try XCTUnwrap(CGContext(data: nil, width: 100, height: 100, bitsPerComponent: 8,
            bytesPerRow: 400, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: rgba))
        raster.draw(picture, in: CGRect(x: 0, y: 0, width: 100, height: 100))
        let pixels = try XCTUnwrap(raster.data?.assumingMemoryBound(to: UInt8.self))
        // Read JPEG scanlines from the top-left, independently of the sheet's
        // drawing coordinates. A vertical swap must invalidate the cited regions.
        // Check distinguishable color classes, not device/PDF/JPEG channel
        // equality: correct primary tiles can have nonzero secondary channels.
        for index in 0..<4 {
            let x = index % 2 * 50 + 25, y = index / 2 * 50 + 25
            let offset = y * raster.bytesPerRow + x * 4
            let red = Int(pixels[offset]), green = Int(pixels[offset + 1]), blue = Int(pixels[offset + 2])
            switch index {
            case 0:
                XCTAssertGreaterThan(red, 180)
                XCTAssertGreaterThan(red - green, 100); XCTAssertGreaterThan(red - blue, 100)
            case 1:
                XCTAssertGreaterThan(green, 180)
                XCTAssertGreaterThan(green - red, 100); XCTAssertGreaterThan(green - blue, 100)
            case 2:
                XCTAssertGreaterThan(blue, 180)
                XCTAssertGreaterThan(blue - red, 100); XCTAssertGreaterThan(blue - green, 100)
            default:
                XCTAssertGreaterThan(red, 180); XCTAssertGreaterThan(green, 180)
                XCTAssertLessThan(blue, 100); XCTAssertLessThan(abs(red - green), 80)
            }
        }
    }
    func testRequiredVisualFrameCannotBeDroppedToFitTheEnvelope() throws {
        let payload = try NativeMediaFixtures.pdf(["FRAME REQUIRED"])
        let request = try NativeMediaRequest(NativeMediaFixtures.args(payload))
        var observation = try NativeMediaDecoder.pdf(request, data: payload,
                                                     cancellation: NativeMediaCancellation(seconds: 10))
        let complete = try observation.value(request)
        let encoded = try JSONEncoder().encode(complete).count
        XCTAssertLessThan(encoded, 131072)
        guard encoded < 131072 else { return }
        // The text/metadata still fit alone. Adding a required frame exceeds
        // the envelope, so silently removing that evidence would be a false success.
        observation.metadata["padding"] = .string(String(repeating: "x", count: 131072 - encoded))
        XCTAssertThrowsError(try observation.value(request)) {
            XCTAssertEqual($0 as? NativeMediaIssue, .limit)
        }
        observation.metadata.removeValue(forKey: "padding"); observation.frame = nil
        XCTAssertThrowsError(try observation.value(request)) {
            XCTAssertEqual($0 as? NativeMediaIssue, .limit)
        }
    }
    @MainActor func testEncryptedOrTooManyPDFPagesAreRejectedRatherThanMisreportedComplete() async throws {
        for (payload, expected) in [
            (try NativeMediaFixtures.pdf(["SECRET FIXTURE"], encrypted: true), NativeMediaIssue.encryptedPDF),
            (try NativeMediaFixtures.pdf(Array(repeating: "PAGE", count: 65)), NativeMediaIssue.limit),
        ] {
            let host = executor(payload)
            let observed = await issue { try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload)) }
            XCTAssertEqual(observed, expected)
        }
    }
    @MainActor func testRTFOnlyExtractsInertPlainTextAndRejectsFieldsObjectsOrPictures() async throws {
        let plain = Data("{\\rtf1\\ansi PRUEBA RTF 6421}".utf8)
        let host = executor(plain)
        let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(plain, kind: "document", mime: "application/rtf", name: "prueba.rtf"))
        XCTAssertEqual(result["segments"].array?.first?["text"], .string("PRUEBA RTF 6421"))
        XCTAssertEqual(result["frame"], .null)
        XCTAssertEqual(result["metadata"]["extraction_policy"], .string("embedded_text_and_visual_v1"))
        XCTAssertEqual(result["metadata"]["ocr_performed"], .bool(false))
        XCTAssertEqual(result["metadata"]["visual_required"], .bool(false))
        XCTAssertEqual(result["metadata"]["local_text_extraction"], .string("rtf_plain_text"))
        for control in ["\\field", "\\object", "\\pict", "\\filetbl", "\\bin"] {
            let payload = Data(("{\\rtf1\\ansi " + control + " PRIVATE EXTERNAL CONTENT}").utf8)
            let denied = executor(payload)
            let observed = await issue { try await denied.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "prueba.rtf")) }
            XCTAssertEqual(observed, .unsupported)
        }
    }
    @MainActor func testMissingAuthorityBadHashChangedLengthAndMismatchedMIMEFailClosed() async throws {
        let payload = try NativeMediaFixtures.png(), args = NativeMediaFixtures.args(payload, kind: "image", mime: "image/png", name: "prueba.png")
        let unbound = NativeMediaExecutor()
        let authority = await issue { try await unbound.execute("media.inspect", arguments: args) }
        XCTAssertEqual(authority, .authorityUnavailable)
        for key in ["source_sha256", "payload_sha256", "byte_length", "extra"] {
            var altered = args
            if key == "byte_length" { altered[key] = .number(Double(payload.count + 1)) }
            else if key == "extra" { altered[key] = .bool(true) }
            else {
                altered["source_sha256"] = .string(String(repeating: "0", count: 64))
                altered["payload_sha256"] = altered["source_sha256"]
            }
            let bound = executor(payload)
            let error = await issue { try await bound.execute("media.inspect", arguments: altered) }
            XCTAssertEqual(error, key == "extra" ? .invalidArguments : .sourceChanged)
        }
        let wrongMIME = executor(payload)
        let mismatch = await issue { try await wrongMIME.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "image", mime: "image/jpeg", name: "prueba.jpg")) }
        XCTAssertEqual(mismatch, .unsupported)
        var tooBig = args; tooBig["byte_length"] = .number(Double(16 * 1024 * 1024 + 1))
        XCTAssertThrowsError(try NativeMediaRequest(tooBig))
    }
    @MainActor func testAudioUsesMemoryResourceAndNeverTranscribesWithoutExplicitAction() async throws {
        let payload = NativeMediaFixtures.wav(), host = executor(payload)
        var transcriptions = 0
        host.transcribe = { data, name, mime, digest, locale in
            transcriptions += 1
            XCTAssertEqual(data, payload); XCTAssertEqual(name, "prueba.wav"); XCTAssertEqual(mime, "audio/wav")
            return try LocalAudioTranscript(text: "PRUEBA AUDIO LOCAL", localeIdentifier: locale, durationSeconds: 1, sourceSHA256: digest)
        }
        let metadata = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "audio", mime: "audio/wav", name: "prueba.wav"))
        XCTAssertEqual(metadata["segments"], .array([])); XCTAssertEqual(transcriptions, 0)
        XCTAssertEqual(metadata["metadata"]["external_references_opened"], .bool(false))
        let transcript = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "audio", mime: "audio/wav", name: "prueba.wav", action: "transcribe"))
        XCTAssertEqual(transcriptions, 1); XCTAssertEqual(transcript["segments"].array?.first?["text"], .string("PRUEBA AUDIO LOCAL"))
        XCTAssertEqual(transcript["segments"].array?.first?["locator"], .string("time:0-1.000"))
        XCTAssertEqual(transcript["metadata"]["transcript_source_sha256"].string, NativeMediaFixtures.digest(payload))
        XCTAssertEqual(transcript["metadata"]["extraction_policy"], .string("embedded_text_and_visual_v1"))
        XCTAssertEqual(transcript["metadata"]["ocr_performed"], .bool(false))
        XCTAssertEqual(transcript["metadata"]["visual_required"], .bool(false))
        XCTAssertEqual(transcript["metadata"]["local_text_extraction"], .string("local_transcript"))
    }
    @MainActor func testVideoIsSampledLocallyAndCannotClaimFullObservation() async throws {
        let payload = try await NativeMediaFixtures.video(), host = executor(payload)
        let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "video", mime: "video/mp4", name: "prueba.mp4"))
        XCTAssertEqual(result["segments"].array?.count, 3); XCTAssertEqual(result["truncated"], .bool(true))
        XCTAssertEqual(result["metadata"]["sampling"], .string("start_middle_end_nearest_frame"))
        XCTAssertEqual(result["metadata"]["sampled_times"].array?.count, 3)
        let times = try XCTUnwrap(result["metadata"]["sampled_times"].array).compactMap { item -> Double? in
            if case .number(let value) = item { return value }; return nil
        }
        XCTAssertEqual(times.count, 3); guard times.count == 3 else { return }
        XCTAssertEqual(Set(times).count, 3, "Tres objetivos no pueden observar sólo el keyframe inicial")
        XCTAssertGreaterThan(times[1], 0.3); XCTAssertGreaterThan(times[2], 0.9)
        XCTAssertEqual(result["metadata"]["generator_tolerance_seconds"], .number(0))
        let segments = try XCTUnwrap(result["segments"].array)
        for segment in segments {
            XCTAssertTrue(segment["locator"].string?.hasPrefix("frame:") == true)
            XCTAssertEqual(segment["text"], .string(""))
        }
        let locators = try segments.map { try XCTUnwrap($0["locator"].string) }
        try assertVisual(result, locators: locators, columns: 3, localTextExtraction: "none")
        try assertFrame(result)
    }
    @MainActor func testVideoTranscriptionDoesNotPrepareVisualFramesOrRecognizeText() async throws {
        let payload = try await NativeMediaFixtures.video(), host = executor(payload)
        var transcriptions = 0
        host.transcribe = { data, name, mime, digest, locale in
            transcriptions += 1
            XCTAssertEqual(data, payload); XCTAssertEqual(name, "prueba.mp4"); XCTAssertEqual(mime, "video/mp4")
            return try LocalAudioTranscript(text: "TRANSCRIPCIÓN SINTÉTICA", localeIdentifier: locale,
                                            durationSeconds: 1.5, sourceSHA256: digest)
        }
        let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload,
            kind: "video", mime: "video/mp4", name: "prueba.mp4", action: "transcribe"))
        XCTAssertEqual(transcriptions, 1)
        XCTAssertEqual(result["segments"], .array([
            .object(["locator": .string("time:0-1.500"), "text": .string("TRANSCRIPCIÓN SINTÉTICA")])]))
        XCTAssertEqual(result["frame"], .null)
        XCTAssertEqual(result["metadata"]["extraction_policy"], .string("embedded_text_and_visual_v1"))
        XCTAssertEqual(result["metadata"]["ocr_performed"], .bool(false))
        XCTAssertEqual(result["metadata"]["visual_required"], .bool(false))
        XCTAssertEqual(result["metadata"]["visual_analysis"], .string("none"))
        XCTAssertEqual(result["metadata"]["visual_locators"], .array([]))
        XCTAssertEqual(result["metadata"]["local_text_extraction"], .string("local_transcript"))
        XCTAssertEqual(result["metadata"]["sampled_times"], .null)
        XCTAssertEqual(result["metadata"]["visual_regions"], .null)
        XCTAssertEqual(result["metadata"]["dimensions"], .null)
    }
    func testDistantKeyframeCannotMasqueradeAsMiddleOrEndObservation() throws {
        XCTAssertNoThrow(try NativeMediaDecoder.checkedSampleTime(0, requested: 0, duration: 2, framePeriod: 0.5))
        XCTAssertNoThrow(try NativeMediaDecoder.checkedSampleTime(1.5, requested: 1.999, duration: 2, framePeriod: 0.5))
        for requested in [1.0, 1.999] {
            XCTAssertThrowsError(try NativeMediaDecoder.checkedSampleTime(0, requested: requested, duration: 2, framePeriod: 0.5)) {
                XCTAssertEqual($0 as? NativeMediaIssue, .decoderFailed)
            }
        }
        XCTAssertThrowsError(try NativeMediaDecoder.checkedSampleTime(.nan, requested: 0, duration: 2, framePeriod: 0.5))
    }
    @MainActor func testRevocationIsCheckedAfterLoadingBeforeDecoderOrTranscriber() async throws {
        let payload = NativeMediaFixtures.wav(), host = NativeMediaExecutor()
        var checks = 0, decoded = false
        host.beforeEffect = { _ in checks += 1; if checks > 1 { throw NativeMediaIssue.authorityUnavailable } }
        host.loadPayload = { _ in payload }
        host.transcribe = { _, _, _, digest, locale in decoded = true; return try LocalAudioTranscript(text: "BAD", localeIdentifier: locale, durationSeconds: 1, sourceSHA256: digest) }
        let error = await issue { try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "audio", mime: "audio/wav", name: "prueba.wav", action: "transcribe")) }
        XCTAssertEqual(error, .authorityUnavailable); XCTAssertFalse(decoded)
    }
    @MainActor func testCancellationJoinsHeldPayloadAndDoesNotReplayAfterReset() async throws {
        let payload = Data("{\\rtf1 HELD}".utf8), host = NativeMediaExecutor()
        let args = NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "prueba.rtf")
        var held: CheckedContinuation<Data, Never>?, entered = false, cancelled = false
        host.beforeEffect = { _ in }
        host.loadPayload = { _ in entered = true; return await withCheckedContinuation { held = $0 } }
        let running = Task { @MainActor in await self.issue { try await host.execute("media.inspect", arguments: args) } }
        for _ in 0..<100 where !entered { await Task.yield() }
        XCTAssertTrue(entered)
        let cancel = Task { @MainActor in await host.cancel(operationID: args["operation_id"]!.string!); cancelled = true }
        for _ in 0..<10 { await Task.yield() }
        XCTAssertFalse(cancelled, "La señal de cancelar no equivale al recibo de limpieza")
        let busy = await issue { try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "otra.rtf")) }
        XCTAssertEqual(busy, .busy)
        held?.resume(returning: payload); held = nil
        let observed = await running.value; await cancel.value
        XCTAssertEqual(observed, .cancelled); XCTAssertTrue(cancelled)
        await host.reset()
        let replay = await issue { try await host.execute("media.inspect", arguments: args) }
        XCTAssertEqual(replay, .alreadySubmitted)
    }
    @MainActor func testDeadlineWaitsForActualLoaderCleanup() async throws {
        let payload = Data("{\\rtf1 HELD}".utf8), host = try NativeMediaExecutor(processingSeconds: 0.02)
        var held: CheckedContinuation<Data, Never>?, entered = false, finished = false
        host.beforeEffect = { _ in }
        host.loadPayload = { _ in entered = true; return await withCheckedContinuation { held = $0 } }
        let running = Task { @MainActor in
            let error = await self.issue { try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "document", mime: "application/rtf", name: "prueba.rtf")) }
            finished = true; return error
        }
        for _ in 0..<100 where !entered { await Task.yield() }
        XCTAssertTrue(entered); try await Task.sleep(for: .milliseconds(60)); XCTAssertFalse(finished)
        held?.resume(returning: payload); held = nil
        let error = await running.value; XCTAssertEqual(error, .deadline)
    }
    func testReplacingSegmentsResetsTextBudgetAndUTF8ClippingIsExplicit() throws {
        var observation = NativeMediaObservation()
        try observation.add("image:1", String(repeating: "Ω", count: 30000))
        XCTAssertTrue(observation.truncated)
        observation.clearSegments(); try observation.add("time:0-1", "TRANSCRIPCIÓN COMPLETA")
        XCTAssertEqual(observation.segments.map(\.text), ["TRANSCRIPCIÓN COMPLETA"])
    }
}
