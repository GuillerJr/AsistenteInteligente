@preconcurrency import AVFoundation
import Accelerate
import Foundation

public enum SpeechInputProcessorError: Error, Equatable {
    case permissionDenied
    case invalidInputFormat
    case vadUnavailable
    case engineFailed
}

public struct SpeechInputFrame: Sendable {
    public let rms: Float
    public let speechProbability: Float?
    public let vadEvent: SileroVADEvent?
    public let samples16k: [Float]
}

public final class SpeechInputProcessor: @unchecked Sendable {
    public static let noiseGateDecibels: Float = -45
    public static let compressorThresholdDecibels: Float = -9
    public static let compressorRatio: Float = 3
    public static let canonicalSpeechSampleRate: Double = 16_000

    static let canonicalSpeechFormat = AVAudioFormat(
        standardFormatWithSampleRate: canonicalSpeechSampleRate,
        channels: 1
    )!

    private let engine: AVAudioEngine
    private let vad: SileroVADDetector
    private let lock = NSLock()
    private var running = false

    public init(
        engine: AVAudioEngine = AVAudioEngine(),
        vad: SileroVADDetector? = nil
    ) throws {
        guard MicrophonePermission.current == .authorized else {
            throw SpeechInputProcessorError.permissionDenied
        }
        self.engine = engine
        do {
            self.vad = try vad ?? SileroVADDetector()
        } catch {
            throw SpeechInputProcessorError.vadUnavailable
        }
    }

    public var processingFormat: AVAudioFormat {
        Self.canonicalSpeechFormat
    }

    public func start(
        bufferMilliseconds: Int = 32,
        handler: @escaping @Sendable (AVAudioPCMBuffer, SpeechInputFrame) -> Void,
        failureHandler: @escaping @Sendable (SpeechInputProcessorError) -> Void
    ) throws {
        guard (20 ... 100).contains(bufferMilliseconds) else {
            throw SpeechInputProcessorError.invalidInputFormat
        }
        let accepted = lock.withLock {
            guard !running else { return false }
            running = true
            return true
        }
        guard accepted else { throw SpeechInputProcessorError.engineFailed }
        do {
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate >= 16_000, format.channelCount > 0 else {
                throw SpeechInputProcessorError.invalidInputFormat
            }
            let frames = AVAudioFrameCount(
                min(max(Int(format.sampleRate * Double(bufferMilliseconds) / 1_000), 256), 8_192)
            )
            // `nil` is intentional: the input device chooses its actual hardware
            // format after the engine starts. Pinning the tap to the format observed
            // before startup made Core Audio build a stale 2ch/44.1 kHz converter for
            // the mono/48 kHz microphone and Apple Speech received an empty stream.
            // Every callback is normalized below to stable mono/16 kHz PCM in memory.
            input.installTap(onBus: 0, bufferSize: frames, format: nil) {
                [weak self] buffer, _ in
                guard let self else { return }
                do {
                    guard
                        let speechBuffer = Self.makeCanonicalSpeechBuffer(from: buffer),
                        let processed = Self.copyAndProcess(buffer)
                    else {
                        failureHandler(.invalidInputFormat)
                        return
                    }
                    let mono16k = Self.resampleMonoTo16k(processed)
                    let observations = try vad.append(samples: mono16k)
                    let observation = observations.last
                    handler(
                        speechBuffer,
                        SpeechInputFrame(
                            rms: Self.rms(processed),
                            speechProbability: observation?.speechProbability,
                            vadEvent: observation?.event,
                            samples16k: mono16k
                        )
                    )
                } catch {
                    failureHandler(.vadUnavailable)
                }
            }
            engine.prepare()
            try engine.start()
        } catch let error as SpeechInputProcessorError {
            stop()
            throw error
        } catch {
            stop()
            throw SpeechInputProcessorError.engineFailed
        }
    }

    public func stop() {
        let shouldStop = lock.withLock {
            guard running else { return false }
            running = false
            return true
        }
        guard shouldStop else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        engine.reset()
        vad.reset()
    }

    private static func copyAndProcess(_ source: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard
            source.format.commonFormat == .pcmFormatFloat32,
            !source.format.isInterleaved,
            source.frameLength > 0,
            let target = AVAudioPCMBuffer(
                pcmFormat: source.format,
                frameCapacity: source.frameLength
            ),
            let sourceChannels = source.floatChannelData,
            let targetChannels = target.floatChannelData
        else {
            return nil
        }
        target.frameLength = source.frameLength
        let count = Int(source.frameLength)
        let threshold = powf(10, compressorThresholdDecibels / 20)
        for channelIndex in 0 ..< Int(source.format.channelCount) {
            let sourceChannel = sourceChannels[channelIndex]
            let targetChannel = targetChannels[channelIndex]
            targetChannel.update(from: sourceChannel, count: count)
            var channelRMS: Float = 0
            vDSP_rmsqv(targetChannel, 1, &channelRMS, vDSP_Length(count))
            let decibels = 20 * log10f(max(channelRMS, 0.000_000_1))
            if decibels < noiseGateDecibels {
                vDSP_vclr(targetChannel, 1, vDSP_Length(count))
                continue
            }
            for index in 0 ..< count {
                let sample = targetChannel[index]
                let magnitude = abs(sample)
                if magnitude > threshold {
                    let compressed = threshold + (magnitude - threshold) / compressorRatio
                    targetChannel[index] = min(compressed, 1) * (sample < 0 ? -1 : 1)
                }
            }
        }
        return target
    }

    private static func rms(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let channel = buffer.floatChannelData?.pointee, buffer.frameLength > 0 else {
            return 0
        }
        var result: Float = 0
        vDSP_rmsqv(channel, 1, &result, vDSP_Length(buffer.frameLength))
        return result
    }

    static func makeCanonicalSpeechBuffer(
        from source: AVAudioPCMBuffer
    ) -> AVAudioPCMBuffer? {
        let samples = resampleMonoTo16k(source)
        guard
            !samples.isEmpty,
            let target = AVAudioPCMBuffer(
                pcmFormat: canonicalSpeechFormat,
                frameCapacity: AVAudioFrameCount(samples.count)
            ),
            let targetChannel = target.floatChannelData?.pointee
        else {
            return nil
        }
        target.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { storage in
            guard let baseAddress = storage.baseAddress else { return }
            targetChannel.update(from: baseAddress, count: storage.count)
        }
        return target
    }

    private static func resampleMonoTo16k(_ buffer: AVAudioPCMBuffer) -> [Float] {
        guard
            let channels = buffer.floatChannelData,
            buffer.frameLength > 0,
            buffer.format.sampleRate > 0
        else {
            return []
        }
        let sourceCount = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        var mono = [Float](repeating: 0, count: sourceCount)
        for channelIndex in 0 ..< channelCount {
            var scale = Float(1) / Float(channelCount)
            vDSP_vsma(
                channels[channelIndex],
                1,
                &scale,
                mono,
                1,
                &mono,
                1,
                vDSP_Length(sourceCount)
            )
        }
        let ratio = Double(SileroSpeechEndpoint.sampleRate) / buffer.format.sampleRate
        let outputCount = max(1, Int((Double(sourceCount) * ratio).rounded(.down)))
        if outputCount == sourceCount { return mono }
        var output = [Float](repeating: 0, count: outputCount)
        let sourceScale = Double(sourceCount - 1) / Double(max(outputCount - 1, 1))
        for index in 0 ..< outputCount {
            let position = Double(index) * sourceScale
            let lower = Int(position)
            let upper = min(lower + 1, sourceCount - 1)
            let fraction = Float(position - Double(lower))
            output[index] = mono[lower] + (mono[upper] - mono[lower]) * fraction
        }
        return output
    }

    deinit {
        stop()
    }
}
