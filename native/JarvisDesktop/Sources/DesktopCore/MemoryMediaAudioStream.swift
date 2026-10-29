import AVFoundation
import CoreMedia
import Foundation

/// Pull-based PCM: the consumer owns backpressure. No producer queues a full
/// recording and each read is capped at 8192 frames / 64 KiB.
public struct MemoryMediaAudioSequence: AsyncSequence, Sendable {
    public typealias Element = AVAudioPCMBuffer
    public struct AsyncIterator: AsyncIteratorProtocol {
        fileprivate let reader: MemoryMediaAudioReader
        public mutating func next() async throws -> AVAudioPCMBuffer? { try await reader.next() }
    }
    fileprivate let reader: MemoryMediaAudioReader
    public func makeAsyncIterator() -> AsyncIterator { AsyncIterator(reader: reader) }
}

public final class MemoryMediaAudioStream: @unchecked Sendable {
    public let buffers: MemoryMediaAudioSequence
    public let duration: TimeInterval
    public let format: AVAudioFormat
    private let reader: MemoryMediaAudioReader
    fileprivate init(reader: MemoryMediaAudioReader, duration: TimeInterval, format: AVAudioFormat) {
        self.reader = reader; self.duration = duration; self.format = format
        buffers = MemoryMediaAudioSequence(reader: reader)
    }
    /// Wait for the same reader actor to retire a pending read before releasing
    /// the memory resource; a cancellation request alone is not this receipt.
    public func close() async { await reader.close() }
}

fileprivate actor MemoryMediaAudioReader {
    let source: MemoryMediaAsset
    let reader: AVAssetReader
    let output: AVAssetReaderTrackOutput
    let format: AVAudioFormat
    let maximumFrames: Int
    var total = 0
    var sample: CMSampleBuffer?
    var offset = 0
    var stopped = false

    init(source: MemoryMediaAsset, reader: AVAssetReader, output: AVAssetReaderTrackOutput,
         format: AVAudioFormat, maximumFrames: Int) {
        self.source = source; self.reader = reader; self.output = output
        self.format = format; self.maximumFrames = maximumFrames
    }
    func next() throws -> AVAudioPCMBuffer? {
        try Task.checkCancellation()
        guard !stopped else { return nil }
        if sample == nil {
            sample = output.copyNextSampleBuffer(); offset = 0
            if sample == nil {
                let succeeded = reader.status == .completed
                close()
                guard succeeded else { throw LocalVoiceIssue.recognitionFailed }
                return nil
            }
        }
        guard let sample else { return nil }
        let available = CMSampleBufferGetNumSamples(sample)
        guard available > 0, available <= 262144, offset < available else {
            close(); throw LocalVoiceIssue.textLimit
        }
        let frames = min(8192, available - offset)
        guard total + frames <= maximumFrames,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)) else {
            close(); throw LocalVoiceIssue.textLimit
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        let bytes = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList).reduce(0) { $0 + Int($1.mDataByteSize) }
        guard bytes > 0, bytes <= 65536,
              CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: Int32(offset), frameCount: Int32(frames),
                                                          into: buffer.mutableAudioBufferList) == noErr else {
            close(); throw LocalVoiceIssue.recognitionFailed
        }
        offset += frames; total += frames
        if offset == available { self.sample = nil }
        try Task.checkCancellation()
        return buffer
    }
    func close() {
        guard !stopped else { return }
        stopped = true; reader.cancelReading(); sample = nil; source.close()
    }
}

public extension MemoryMediaAsset {
    func audioBuffers(format: AVAudioFormat, maxDuration: TimeInterval = 120) async throws -> MemoryMediaAudioStream {
        guard maxDuration.isFinite, maxDuration > 0, maxDuration <= 120,
              format.sampleRate.isFinite, format.sampleRate > 0, format.sampleRate <= 96000,
              (1...2).contains(format.channelCount),
              [.pcmFormatFloat32, .pcmFormatInt16].contains(format.commonFormat) else { throw LocalVoiceIssue.configuration }
        let duration = try await asset.load(.duration).seconds
        guard duration.isFinite, duration > 0, duration <= maxDuration else { throw LocalVoiceIssue.textLimit }
        try Task.checkCancellation()
        guard let track = try await asset.loadTracks(withMediaType: .audio).first else { throw LocalVoiceIssue.recognitionFailed }
        let reader = try AVAssetReader(asset: asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: Int(format.channelCount),
            AVLinearPCMBitDepthKey: Int(format.streamDescription.pointee.mBitsPerChannel),
            AVLinearPCMIsFloatKey: format.commonFormat == .pcmFormatFloat32,
            AVLinearPCMIsNonInterleaved: !format.isInterleaved,
        ])
        output.alwaysCopiesSampleData = false
        guard reader.canAdd(output) else { throw LocalVoiceIssue.recognitionFailed }
        reader.add(output)
        guard reader.startReading() else { throw LocalVoiceIssue.recognitionFailed }
        let owner = MemoryMediaAudioReader(source: self, reader: reader, output: output, format: format,
                                           maximumFrames: Int(ceil(maxDuration * format.sampleRate)))
        return MemoryMediaAudioStream(reader: owner, duration: duration, format: format)
    }
}
