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
        let size = try await track.load(.naturalSize), transform = try await track.load(.preferredTransform)
        let transformed = size.applying(transform), width = abs(transformed.width), height = abs(transformed.height)
        guard width.isFinite, height.isFinite, width > 0, height > 0,
              width <= 40000000, height <= 40000000, width * height <= 40000000 else { throw NativeMediaIssue.limit }
        let generator = AVAssetImageGenerator(asset: source.asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 1200, height: 1200)
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
            guard actual.isFinite, actual >= 0, actual <= duration + 0.1 else { throw NativeMediaIssue.decoderFailed }
            try cancellation.check()
            let text = try recognize(sample.image, locale: request.locale, cancellation: cancellation)
            let locator = "frame:\(index + 1)@" + String(format: "%.3f", locale: Locale(identifier: "en_US_POSIX"), actual)
            try result.add(locator, text)
            pictures.append(sample.image); actualTimes.append(.number(actual))
        }
        result.metadata["dimensions"] = .object(["width": .number(width.rounded()), "height": .number(height.rounded())])
        result.metadata["sampled_times"] = .array(actualTimes)
        result.metadata["sampling"] = .string("start_middle_end_nearest_frame")
        result.metadata["ocr_on_device"] = .bool(true)
        result.truncated = true // Three samples never assert observation of an entire video.
        result.frame = try frame(contactSheet(pictures, columns: 3), cancellation: cancellation)
        return result
    }
}
