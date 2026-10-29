@preconcurrency import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation

extension NativeMediaDecoder {
    static func audiovisual(_ request: NativeMediaRequest, data: Data,
                            cancellation: NativeMediaCancellation) async throws -> NativeMediaObservation {
        try cancellation.check()
        let source = try MemoryMediaAsset(data: data, mimeType: request.mime, identifier: request.operationID)
        let sourceRegistration = cancellation.register { source.close() }
        defer { cancellation.remove(sourceRegistration); source.close() }
        let duration: Double
        let videoTracks: [AVAssetTrack], audioTracks: [AVAssetTrack]
        do {
            duration = try await source.asset.load(.duration).seconds
            videoTracks = try await source.asset.loadTracks(withMediaType: .video)
            audioTracks = try await source.asset.loadTracks(withMediaType: .audio)
        } catch { try cancellation.check(); throw NativeMediaIssue.decoderFailed }
        try cancellation.check()
        guard duration.isFinite, duration > 0, duration <= 120,
              videoTracks.count <= 8, audioTracks.count <= 8 else { throw NativeMediaIssue.limit }
        guard request.kind == "video" ? !videoTracks.isEmpty : (videoTracks.isEmpty && !audioTracks.isEmpty) else {
            throw NativeMediaIssue.unsupported
        }
        var result = NativeMediaObservation()
        result.metadata = ["duration_seconds": .number(duration),
                           "audio_track_count": .number(Double(audioTracks.count)),
                           "video_track_count": .number(Double(videoTracks.count)),
                           "external_references_opened": .bool(false),
                           "transcription_requested": .bool(request.action == "transcribe")]
        guard request.kind == "video", let track = videoTracks.first else { return result }
        let size: CGSize, transform: CGAffineTransform
        let minimumPeriod: Double, nominalRate: Double
        do {
            size = try await track.load(.naturalSize)
            transform = try await track.load(.preferredTransform)
            minimumPeriod = try await track.load(.minFrameDuration).seconds
            nominalRate = Double(try await track.load(.nominalFrameRate))
        } catch { try cancellation.check(); throw NativeMediaIssue.decoderFailed }
        try cancellation.check()
        let dimensions = try checkedVideoDimensions(size, transform: transform)
        let width = dimensions.width, height = dimensions.height
        let framePeriod = minimumPeriod.isFinite && minimumPeriod > 0 ? minimumPeriod : 1 / nominalRate
        guard framePeriod.isFinite, framePeriod > 0, framePeriod <= 120 else { throw NativeMediaIssue.unsupported }
        let generator = AVAssetImageGenerator(asset: source.asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 1200, height: 1200)
        // The default tolerance is infinite and can return the first keyframe
        // for all three targets. Exact requests force decoding at each target.
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        let generatorRegistration = cancellation.register { generator.cancelAllCGImageGeneration() }
        defer { cancellation.remove(generatorRegistration); generator.cancelAllCGImageGeneration() }
        let requested = [0.0, duration / 2, max(0, duration - 0.001)]
        var pictures: [CGImage] = [], actualTimes: [RPCValue] = []
        for (index, seconds) in requested.enumerated() {
            try cancellation.check()
            let sample: (image: CGImage, actualTime: CMTime)
            do { sample = try await generator.image(at: CMTime(seconds: seconds, preferredTimescale: 600)) }
            catch { try cancellation.check(); throw NativeMediaIssue.decoderFailed }
            let actual = sample.actualTime.seconds
            try checkedSampleTime(actual, requested: seconds, duration: duration, framePeriod: framePeriod)
            try cancellation.check()
            let text = try recognize(sample.image, locale: request.locale, cancellation: cancellation)
            let locator = "frame:\(index + 1)@" + String(format: "%.3f", locale: Locale(identifier: "en_US_POSIX"), actual)
            try result.add(locator, text)
            pictures.append(sample.image); actualTimes.append(.number(actual))
        }
        result.metadata["dimensions"] = .object(["width": .number(width.rounded()), "height": .number(height.rounded())])
        result.metadata["sampled_times"] = .array(actualTimes)
        result.metadata["requested_times"] = .array(requested.map(RPCValue.number))
        result.metadata["frame_period_seconds"] = .number(framePeriod)
        result.metadata["generator_tolerance_seconds"] = .number(0)
        result.metadata["sampling"] = .string("start_middle_end_nearest_frame")
        result.metadata["ocr_on_device"] = .bool(true)
        result.truncated = true // Three samples never assert observation of an entire video.
        result.frame = try frame(contactSheet(pictures, columns: 3), cancellation: cancellation)
        return result
    }

    static func checkedSampleTime(_ actual: Double, requested: Double, duration: Double, framePeriod: Double) throws {
        guard actual.isFinite, requested.isFinite, duration.isFinite, framePeriod.isFinite,
              actual >= 0, requested >= 0, requested < duration, duration > 0, duration <= 120,
              framePeriod > 0, framePeriod <= 120,
              actual <= duration, abs(actual - requested) <= framePeriod + 1 / 600.0 else {
            throw NativeMediaIssue.decoderFailed
        }
    }

    static func checkedVideoDimensions(_ size: CGSize, transform: CGAffineTransform) throws -> CGSize {
        // A presentation scale must not conceal a raster that exceeds the
        // decoder budget. Bound encoded dimensions before any image request.
        guard size.width.isFinite, size.height.isFinite, size.width >= 1, size.height >= 1,
              size.width <= 40000000, size.height <= 40000000,
              size.width * size.height <= 40000000,
              [transform.a, transform.b, transform.c, transform.d, transform.tx, transform.ty].allSatisfy(\.isFinite)
        else { throw NativeMediaIssue.limit }
        let transformed = CGRect(origin: .zero, size: size).applying(transform)
        let width = transformed.width, height = transformed.height
        guard transformed.origin.x.isFinite, transformed.origin.y.isFinite,
              width.isFinite, height.isFinite, width >= 1, height >= 1,
              width <= 40000000, height <= 40000000, width * height <= 40000000 else { throw NativeMediaIssue.limit }
        return CGSize(width: width, height: height)
    }
}
