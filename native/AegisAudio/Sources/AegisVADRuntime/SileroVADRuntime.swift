import Foundation
import OnnxRuntimeBindings

public enum VADRuntimeError: Error { case invalidModel, invalidInput, inferenceFailed }

/// The sole ONNX consumer. This module is linked only into jarvis-vad-helper.
public final class SileroVADRuntime {
    private static let contextSamples = 64
    private static let stateElements = 256
    private let session: ORTSession
    private var context = [Float](repeating: 0, count: contextSamples)
    private var recurrentState = [Float](repeating: 0, count: stateElements)

    public convenience init() throws {
        // A command-line helper does not inherit its parent's Bundle.main.
        // Resolve both packaged and SwiftPM resources from this executable only.
        let directory = URL(fileURLWithPath: CommandLine.arguments[0])
            .standardizedFileURL.deletingLastPathComponent()
        let candidates = [
            directory.deletingLastPathComponent().appendingPathComponent("Resources"),
            directory,
        ]
        for candidate in candidates {
            let resourceBundle = candidate.appendingPathComponent("AegisAudio_AegisVADRuntime.bundle")
            if let bundle = Bundle(url: resourceBundle),
               let url = bundle.url(forResource: "silero-vad", withExtension: "onnx") {
                try self.init(modelURL: url)
                return
            }
        }
        throw VADRuntimeError.invalidModel
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
            throw VADRuntimeError.invalidModel
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
                throw VADRuntimeError.invalidModel
            }
        } catch let error as VADRuntimeError {
            throw error
        } catch {
            throw VADRuntimeError.invalidModel
        }
    }

    public func reset() {
        context = [Float](repeating: 0, count: Self.contextSamples)
        recurrentState = [Float](repeating: 0, count: Self.stateElements)
    }

    public func infer(chunk: [Float]) throws -> Float {
        guard chunk.count == 512 else {
            throw VADRuntimeError.invalidInput
        }
        var framedInput = context
        framedInput.append(contentsOf: chunk)
        let inputData = Self.mutableData(framedInput)
        let stateData = Self.mutableData(recurrentState)
        var sampleRate = Int64(16000)
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
                throw VADRuntimeError.inferenceFailed
            }
            let probability = probabilityData.bytes.load(as: Float.self)
            guard probability.isFinite, (0 ... 1).contains(probability) else {
                throw VADRuntimeError.inferenceFailed
            }
            recurrentState = Array(
                UnsafeBufferPointer(
                    start: stateOutput.bytes.assumingMemoryBound(to: Float.self),
                    count: Self.stateElements
                )
            )
            context = Array(chunk.suffix(Self.contextSamples))
            return probability
        } catch let error as VADRuntimeError {
            throw error
        } catch {
            throw VADRuntimeError.inferenceFailed
        }
    }

    private static func mutableData(_ values: [Float]) -> NSMutableData {
        values.withUnsafeBytes { bytes in
            NSMutableData(bytes: bytes.baseAddress, length: bytes.count)
        }
    }
}
