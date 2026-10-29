@preconcurrency import AVFoundation
import CryptoKit
import Foundation
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
    @MainActor private func executor(_ payload: Data) -> NativeMediaExecutor {
        let result = NativeMediaExecutor()
        result.beforeEffect = { _ in }; result.loadPayload = { _ in payload }
        return result
    }
    @MainActor private func issue(_ operation: () async throws -> RPCValue) async -> NativeMediaIssue? {
        do { _ = try await operation(); XCTFail("Se esperaba un rechazo local"); return nil }
        catch { return error as? NativeMediaIssue }
    }
    private func assertFrame(_ result: RPCValue, file: StaticString = #filePath, line: UInt = #line) throws {
        let frame = result["frame"]
        XCTAssertEqual(frame["storage"], .string("memory_only"), file: file, line: line)
        let url = try XCTUnwrap(frame["data_url"].string, file: file, line: line)
        XCTAssertTrue(url.hasPrefix("data:image/jpeg;base64,"), file: file, line: line)
        let bytes = try XCTUnwrap(Data(base64Encoded: String(url.dropFirst(23))), file: file, line: line)
        XCTAssertLessThanOrEqual(bytes.count, 49152, file: file, line: line)
        XCTAssertEqual(frame["bytes"].integer, bytes.count, file: file, line: line)
        XCTAssertEqual(frame["sha256"].string, NativeMediaFixtures.digest(bytes), file: file, line: line)
        XCTAssertNil(result["metadata"]["data_url"].string, file: file, line: line)
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
        try assertFrame(result)
    }
    @MainActor func testScannedPDFAndPNGRunRealOfflineVisionWithSourceCitations() async throws {
        for (payload, kind, mime, name, locator) in [
            (try NativeMediaFixtures.png(), "image", "image/png", "prueba.png", "image:1"),
            (try NativeMediaFixtures.pdf(["JARVIS LOCAL IMAGE 4826"], scanned: true), "pdf", "application/pdf", "scan.pdf", "page:1"),
        ] {
            let host = executor(payload)
            let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: kind, mime: mime, name: name))
            XCTAssertEqual(result["on_device"], .bool(true)); XCTAssertEqual(result["metadata"]["ocr_on_device"], .bool(true))
            XCTAssertEqual(result["segments"].array?.first?["locator"], .string(locator))
            XCTAssertTrue(result["segments"].array?.first?["text"].string?.contains("4826") == true)
            try assertFrame(result)
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
    }
    @MainActor func testVideoIsSampledLocallyAndCannotClaimFullObservation() async throws {
        let payload = try await NativeMediaFixtures.video(), host = executor(payload)
        let result = try await host.execute("media.inspect", arguments: NativeMediaFixtures.args(payload, kind: "video", mime: "video/mp4", name: "prueba.mp4"))
        XCTAssertEqual(result["segments"].array?.count, 3); XCTAssertEqual(result["truncated"], .bool(true))
        XCTAssertEqual(result["metadata"]["sampling"], .string("start_middle_end_nearest_frame"))
        XCTAssertEqual(result["metadata"]["sampled_times"].array?.count, 3)
        for segment in result["segments"].array ?? [] { XCTAssertTrue(segment["locator"].string?.hasPrefix("frame:") == true) }
        try assertFrame(result)
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
    func testReplacingOCRSegmentsResetsTextBudgetAndUTF8ClippingIsExplicit() throws {
        var observation = NativeMediaObservation()
        try observation.add("image:1", String(repeating: "Ω", count: 30000))
        XCTAssertTrue(observation.truncated)
        observation.clearSegments(); try observation.add("time:0-1", "TRANSCRIPCIÓN COMPLETA")
        XCTAssertEqual(observation.segments.map(\.text), ["TRANSCRIPCIÓN COMPLETA"])
    }
}
