import AVFoundation
import Foundation
import UniformTypeIdentifiers

/// A bounded, local-only AVFoundation resource. Encoded bytes remain in RAM,
/// including for temporary conversations. No URL supplied by a document is
/// followed, and the loader never falls through to HTTP or the filesystem.
public final class MemoryMediaAsset: NSObject, AVAssetResourceLoaderDelegate, @unchecked Sendable {
    public let asset: AVURLAsset
    private let data: Data
    private let contentType: String
    private let url: URL
    private let queue = DispatchQueue(label: "jarvis.media.memory")
    private var closed = false

    public init(data: Data, mimeType: String, identifier: String = UUID().uuidString) throws {
        guard !data.isEmpty, data.count <= 16 * 1024 * 1024,
              identifier.utf8.count <= 64,
              identifier.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || $0 == "-" }),
              ["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac",
               "video/mp4", "video/quicktime", "video/webm"].contains(mimeType),
              let url = URL(string: "jarvis-media://local/" + identifier) else { throw RPCFailure.invalidResponse }
        self.data = data; self.url = url
        contentType = UTType(mimeType: mimeType)?.identifier ?? "public.data"
        asset = AVURLAsset(url: url, options: [AVURLAssetOutOfBandMIMETypeKey: mimeType])
        super.init()
        asset.resourceLoader.setDelegate(self, queue: queue)
    }

    public func close() {
        queue.sync { closed = true }
        asset.cancelLoading()
    }

    public func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                               shouldWaitForLoadingOfRequestedResource request: AVAssetResourceLoadingRequest) -> Bool {
        guard !closed, request.request.url == url else {
            request.finishLoading(with: RPCFailure.disconnected); return true
        }
        if let info = request.contentInformationRequest {
            info.contentType = contentType
            info.contentLength = Int64(data.count)
            info.isByteRangeAccessSupported = true
        }
        if let wanted = request.dataRequest {
            let offset = max(wanted.requestedOffset, wanted.currentOffset)
            guard offset >= 0, offset <= Int64(data.count), wanted.requestedLength >= 0 else {
                request.finishLoading(with: RPCFailure.invalidResponse); return true
            }
            let end: Int64
            if wanted.requestsAllDataToEndOfResource { end = Int64(data.count) }
            else {
                let (sum, overflow) = wanted.requestedOffset.addingReportingOverflow(Int64(wanted.requestedLength))
                guard !overflow, sum >= offset else {
                    request.finishLoading(with: RPCFailure.invalidResponse); return true
                }
                end = min(Int64(data.count), sum)
            }
            if end > offset { wanted.respond(with: data.subdata(in: Int(offset)..<Int(end))) }
        }
        request.finishLoading()
        return true
    }
}
