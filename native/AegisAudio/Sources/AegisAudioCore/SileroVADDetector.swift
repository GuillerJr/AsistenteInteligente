import Foundation
import OnnxRuntimeBindings

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

public final class SileroVADDetector: @unchecked Sendable {
    private static let contextSamples = 64
    private static let stateElements = 2 * 1 * 128

    private let session: ORTSession
    private let lock = NSLock()
    private var pendingSamples = [Float]()
    private var context = [Float](repeating: 0, count: contextSamples)
    private var recurrentState = [Float](repeating: 0, count: stateElements)
    private var endpoint = SileroSpeechEndpoint()

    public convenience init(bundle: Bundle = .main) throws {
        let modelURL = bundle.url(forResource: "silero-vad", withExtension: "onnx")
            ?? Bundle.module.url(forResource: "silero-vad", withExtension: "onnx")
        guard let modelURL else { throw SileroVADError.invalidModel }
        try self.init(modelURL: modelURL)
    }

    public init(modelURL: URL) throws {
        let values = try modelURL.resourceValues(forKeys: [
            .isRegularFileKey,
            .fileSizeKey,
        ])
        guard
            values.isRegularFile == true,
            let fileSize = values.fileSize,
            (1_024 ... 16 * 1_024 * 1_024).contains(fileSize)
        else {
            throw SileroVADError.invalidModel
        }
        do {
            let environment = try ORTEnv(loggingLevel: .warning)
            let options = try ORTSessionOptions()
            try options.setIntraOpNumThreads(1)
            try options.setGraphOptimizationLevel(.all)
            session = try ORTSession(
                env: environment,
                modelPath: modelURL.path,
                sessionOptions: options
            )
            let inputs = try session.inputNames()
            let outputs = try session.outputNames()
            guard
                Set(inputs) == Set(["input", "state", "sr"]),
                Set(outputs) == Set(["output", "stateN"])
            else {
                throw SileroVADError.invalidModel
            }
        } catch let error as SileroVADError {
            throw error
        } catch {
            throw SileroVADError.invalidModel
        }
    }

    public func reset() {
        lock.withLock {
            pendingSamples.removeAll(keepingCapacity: true)
            context = [Float](repeating: 0, count: Self.contextSamples)
            recurrentState = [Float](repeating: 0, count: Self.stateElements)
            endpoint = SileroSpeechEndpoint()
        }
    }

    public func append(samples: [Float]) throws -> [SileroVADObservation] {
        guard !samples.isEmpty, samples.allSatisfy(\.isFinite) else {
            throw SileroVADError.invalidInput
        }
        return try lock.withLock {
            pendingSamples.append(contentsOf: samples)
            var observations = [SileroVADObservation]()
            while pendingSamples.count >= SileroSpeechEndpoint.chunkSamples {
                let chunk = Array(pendingSamples.prefix(SileroSpeechEndpoint.chunkSamples))
                pendingSamples.removeFirst(SileroSpeechEndpoint.chunkSamples)
                let probability = try infer(chunk: chunk)
                observations.append(
                    SileroVADObservation(
                        speechProbability: probability,
                        event: endpoint.observe(probability: probability)
                    )
                )
            }
            return observations
        }
    }

    private func infer(chunk: [Float]) throws -> Float {
        guard chunk.count == SileroSpeechEndpoint.chunkSamples else {
            throw SileroVADError.invalidInput
        }
        var framedInput = context
        framedInput.append(contentsOf: chunk)
        let inputData = Self.mutableData(framedInput)
        let stateData = Self.mutableData(recurrentState)
        var sampleRate = Int64(SileroSpeechEndpoint.sampleRate)
        let sampleRateData = NSMutableData(
            bytes: &sampleRate,
            length: MemoryLayout<Int64>.size
        )
        do {
            let input = try ORTValue(
                tensorData: inputData,
                elementType: .float,
                shape: [1, NSNumber(value: framedInput.count)]
            )
            let state = try ORTValue(
                tensorData: stateData,
                elementType: .float,
                shape: [2, 1, 128]
            )
            let rate = try ORTValue(
                tensorData: sampleRateData,
                elementType: .int64,
                shape: [1]
            )
            let outputs = try session.run(
                withInputs: ["input": input, "state": state, "sr": rate],
                outputNames: Set(["output", "stateN"]),
                runOptions: nil
            )
            guard
                let probabilityData = try outputs["output"]?.tensorData(),
                probabilityData.length == MemoryLayout<Float>.size,
                let stateOutput = try outputs["stateN"]?.tensorData(),
                stateOutput.length == Self.stateElements * MemoryLayout<Float>.size
            else {
                throw SileroVADError.inferenceFailed
            }
            let probability = probabilityData.bytes.load(as: Float.self)
            guard probability.isFinite, (0 ... 1).contains(probability) else {
                throw SileroVADError.inferenceFailed
            }
            recurrentState = Array(
                UnsafeBufferPointer(
                    start: stateOutput.bytes.assumingMemoryBound(to: Float.self),
                    count: Self.stateElements
                )
            )
            context = Array(chunk.suffix(Self.contextSamples))
            return probability
        } catch let error as SileroVADError {
            throw error
        } catch {
            throw SileroVADError.inferenceFailed
        }
    }

    private static func mutableData(_ values: [Float]) -> NSMutableData {
        values.withUnsafeBytes { bytes in
            NSMutableData(bytes: bytes.baseAddress, length: bytes.count)
        }
    }
}
