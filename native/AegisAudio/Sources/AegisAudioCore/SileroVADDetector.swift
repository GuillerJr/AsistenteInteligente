import Foundation

public enum SileroVADError: Error, Equatable {
    case invalidModel
    case invalidInput
    case inferenceFailed
}

public enum SileroVADEvent: Equatable, Sendable {
    case speechStarted
    case speechEnded
}

public struct SileroVADObservation: Equatable, Sendable {
    public let speechProbability: Float
    public let event: SileroVADEvent?
}

public struct SileroSpeechEndpoint: Sendable {
    public static let sampleRate = 16_000
    public static let chunkSamples = 512
    public static let speechThreshold: Float = 0.55
    public static let trailingSilenceChunks = 25

    private(set) var speechDetected = false
    private(set) var silenceChunks = 0
    private(set) var ended = false

    public init() {}

    public mutating func observe(probability rawProbability: Float) -> SileroVADEvent? {
        guard !ended, rawProbability.isFinite else { return nil }
        let probability = min(max(rawProbability, 0), 1)
        if probability > Self.speechThreshold {
            silenceChunks = 0
            if !speechDetected {
                speechDetected = true
                return .speechStarted
            }
            return nil
        }
        guard speechDetected else { return nil }
        silenceChunks += 1
        guard silenceChunks >= Self.trailingSilenceChunks else { return nil }
        ended = true
        return .speechEnded
    }
}

/// Voice samples stay in bounded anonymous pipes; only the isolated helper links ONNX.
public final class SileroVADDetector: @unchecked Sendable {
    private let worker: VADProcessClient
    private let lock = NSLock()
    private var pendingSamples = [Float]()
    private var endpoint = SileroSpeechEndpoint()

    public init(bundle: Bundle = .main) throws {
        worker = try VADProcessClient(bundle: bundle)
    }

    public init(modelURL: URL) throws {
        worker = try VADProcessClient(bundle: .main, modelURL: modelURL)
    }

    init(worker: VADProcessClient) { self.worker = worker }

    public func reset() {
        lock.withLock {
            pendingSamples.removeAll(keepingCapacity: true)
            endpoint = SileroSpeechEndpoint()
            worker.reset()
        }
    }

    public func append(samples: [Float]) throws -> [SileroVADObservation] {
        // Capture provides at most a few frames. Bound memory and time for arbitrary callers.
        guard !samples.isEmpty, samples.count <= 4096, samples.allSatisfy(\.isFinite) else {
            throw SileroVADError.invalidInput
        }
        return try lock.withLock {
            pendingSamples.append(contentsOf: samples)
            var observations = [SileroVADObservation]()
            while pendingSamples.count >= SileroSpeechEndpoint.chunkSamples {
                let chunk = Array(pendingSamples.prefix(SileroSpeechEndpoint.chunkSamples))
                pendingSamples.removeFirst(SileroSpeechEndpoint.chunkSamples)
                let probability: Float
                do { probability = try worker.infer(chunk: chunk) }
                catch {
                    // A failed worker must not accumulate frames if the capture
                    // callback reaches us again before its owner stops the tap.
                    pendingSamples.removeAll(keepingCapacity: true)
                    throw error
                }
                observations.append(SileroVADObservation(
                    speechProbability: probability,
                    event: endpoint.observe(probability: probability)
                ))
            }
            return observations
        }
    }
}
