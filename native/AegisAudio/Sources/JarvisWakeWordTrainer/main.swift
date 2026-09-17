import AVFAudio
import AegisAudioCore
import CoreML
import CreateML
import CryptoKit
import Darwin
import Foundation
import SoundAnalysis

private enum TrainingFailure: String, Error {
    case invalidArguments = "invalid_arguments"
    case unsafeDataset = "unsafe_dataset"
    case invalidLabels = "invalid_labels"
    case invalidAudio = "invalid_audio"
    case insufficientSamples = "insufficient_samples"
    case excessiveSamples = "excessive_samples"
    case unsafeOutput = "unsafe_output"
    case poorValidation = "poor_validation"
    case invalidModel = "invalid_model"
    case trainingFailed = "training_failed"
}

private struct TrainingDataset {
    static let labels: Set<String> = ["background", "jarvis"]
    static let audioExtensions: Set<String> = ["aif", "aiff", "caf", "wav"]
    static let minimumSamplesPerLabel = 20
    static let maximumSamplesPerLabel = 500
    static let maximumFileBytes = 5 * 1_024 * 1_024
    static let maximumDatasetBytes = 512 * 1_024 * 1_024

    let filesByLabel: [String: [URL]]
    let fingerprint: String

    static func inspect(root: URL) throws -> TrainingDataset {
        let manager = FileManager.default
        guard manager.fileExists(atPath: root.path) else {
            throw TrainingFailure.unsafeDataset
        }
        let rootValues = try root.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard rootValues.isDirectory == true, rootValues.isSymbolicLink != true else {
            throw TrainingFailure.unsafeDataset
        }
        let entries = try manager.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )
        guard Set(entries.map(\.lastPathComponent)) == labels else {
            throw TrainingFailure.invalidLabels
        }

        var filesByLabel: [String: [URL]] = [:]
        var manifest: [String] = []
        var totalBytes = 0
        for label in labels.sorted() {
            let labelURL = root.appending(path: label, directoryHint: .isDirectory)
            let labelValues = try labelURL.resourceValues(
                forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
            )
            guard labelValues.isDirectory == true, labelValues.isSymbolicLink != true else {
                throw TrainingFailure.unsafeDataset
            }
            let files = try manager.contentsOfDirectory(
                at: labelURL,
                includingPropertiesForKeys: [
                    .fileSizeKey, .isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey,
                ],
                options: [.skipsHiddenFiles]
            ).sorted { $0.lastPathComponent < $1.lastPathComponent }
            guard files.count >= minimumSamplesPerLabel else {
                throw TrainingFailure.insufficientSamples
            }
            guard files.count <= maximumSamplesPerLabel else {
                throw TrainingFailure.excessiveSamples
            }
            for file in files {
                totalBytes += try validateAudio(file)
                guard totalBytes <= maximumDatasetBytes else {
                    throw TrainingFailure.excessiveSamples
                }
                let digest = SHA256.hash(data: try Data(contentsOf: file, options: .mappedIfSafe))
                manifest.append("\(label):\(digest.map { String(format: "%02x", $0) }.joined())")
            }
            filesByLabel[label] = files
        }
        let fingerprint = SHA256.hash(data: Data(manifest.joined(separator: "\n").utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return TrainingDataset(filesByLabel: filesByLabel, fingerprint: fingerprint)
    }

    private static func validateAudio(_ url: URL) throws -> Int {
        let values = try url.resourceValues(forKeys: [
            .fileSizeKey, .isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey,
        ])
        guard
            audioExtensions.contains(url.pathExtension.lowercased()),
            values.isRegularFile == true,
            values.isDirectory != true,
            values.isSymbolicLink != true,
            let size = values.fileSize,
            (1 ... maximumFileBytes).contains(size)
        else {
            throw TrainingFailure.invalidAudio
        }
        let audio = try AVAudioFile(forReading: url)
        let format = audio.processingFormat
        let duration = Double(audio.length) / format.sampleRate
        guard
            (8_000 ... 48_000).contains(format.sampleRate),
            (1 ... 2).contains(format.channelCount),
            duration.isFinite,
            (0.4 ... 3).contains(duration)
        else {
            throw TrainingFailure.invalidAudio
        }
        return size
    }
}

@main
private enum WakeWordTrainer {
    static func main() async {
        do {
            let arguments = CommandLine.arguments
            guard arguments.count == 3 else {
                throw TrainingFailure.invalidArguments
            }
            if arguments[1] == "--check" {
                let dataset = try TrainingDataset.inspect(
                    root: URL(fileURLWithPath: arguments[2]).standardizedFileURL
                )
                print(
                    "status=ready jarvis=\(dataset.filesByLabel["jarvis"]?.count ?? 0) "
                        + "background=\(dataset.filesByLabel["background"]?.count ?? 0)"
                )
                return
            }
            if arguments[1] == "--validate-model" {
                try validateExistingModel(
                    URL(fileURLWithPath: arguments[2]).standardizedFileURL
                )
                print("status=valid model=JarvisWakeWord.mlmodelc")
                return
            }
            let datasetURL = URL(fileURLWithPath: arguments[1]).standardizedFileURL
            let outputURL = URL(fileURLWithPath: arguments[2]).standardizedFileURL
            try validateOutput(outputURL)
            let dataset = try TrainingDataset.inspect(root: datasetURL)
            let manager = FileManager.default
            let temporaryRoot = manager.temporaryDirectory.appending(
                path: "jarvis-wake-training-\(UUID().uuidString)",
                directoryHint: .isDirectory
            )
            try manager.createDirectory(at: temporaryRoot, withIntermediateDirectories: false,
                                        attributes: [.posixPermissions: 0o700])
            defer { try? manager.removeItem(at: temporaryRoot) }
            let trainingFiles = try prepareTrainingWindows(dataset, at: temporaryRoot)
            let jarvisCount = dataset.filesByLabel["jarvis"]?.count ?? 0
            let backgroundCount = dataset.filesByLabel["background"]?.count ?? 0
            print(
                "status=training jarvis=\(jarvisCount) background=\(backgroundCount) "
                    + "dataset_sha256=\(dataset.fingerprint)"
            )

            let parameters = MLSoundClassifier.ModelParameters(
                validation: .split(strategy: .fixed(ratio: 0.2, seed: 42)),
                maxIterations: 25,
                overlapFactor: 0.5,
                algorithm: .transferLearning(
                    featureExtractor: .audioFeaturePrint(type: .sound, revision: 1),
                    classifier: .logisticRegressor
                )
            )
            let classifier = try MLSoundClassifier(
                trainingData: .filesByLabel(trainingFiles),
                parameters: parameters
            )
            print(
                "status=metrics training_valid=\(classifier.trainingMetrics.isValid) "
                    + "validation_valid=\(classifier.validationMetrics.isValid) "
                    + "training_error=\(classifier.trainingMetrics.classificationError) "
                    + "validation_error=\(classifier.validationMetrics.classificationError)"
            )
            guard
                classifier.trainingMetrics.isValid,
                classifier.validationMetrics.isValid,
                classifier.validationMetrics.classificationError <= 0.25
            else {
                throw TrainingFailure.poorValidation
            }

            let sourceModel = temporaryRoot.appending(path: "JarvisWakeWord.mlmodel")
            try classifier.write(
                to: sourceModel,
                metadata: MLModelMetadata(
                    author: "Jarvis",
                    shortDescription: "Local wake word classifier for Jarvis",
                    license: "Private local model",
                    version: "1",
                    additional: [
                        "dataset_sha256": dataset.fingerprint,
                        "labels": "background,jarvis",
                        "keyword_window_policy": "speech_region_v1",
                    ]
                )
            )
            let compiledModel = try await MLModel.compileModel(at: sourceModel)
            try validateCompiledModel(compiledModel)
            try manager.copyItem(at: compiledModel, to: outputURL)
            print(
                "status=ok validation_error="
                    + String(format: "%.4f", classifier.validationMetrics.classificationError)
                    + " dataset_sha256=\(dataset.fingerprint)"
            )
        } catch let failure as TrainingFailure {
            FileHandle.standardError.write(Data("status=error reason=\(failure.rawValue)\n".utf8))
            exit(failure == .invalidArguments ? 2 : 1)
        } catch {
            FileHandle.standardError.write(Data("status=error reason=training_failed\n".utf8))
            exit(1)
        }
    }

    private static func validateOutput(_ outputURL: URL) throws {
        let manager = FileManager.default
        guard
            outputURL.pathExtension == "mlmodelc",
            !manager.fileExists(atPath: outputURL.path)
        else {
            throw TrainingFailure.unsafeOutput
        }
        let parent = outputURL.deletingLastPathComponent()
        let values = try parent.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else {
            throw TrainingFailure.unsafeOutput
        }
    }

    private static func prepareTrainingWindows(
        _ dataset: TrainingDataset,
        at root: URL
    ) throws -> [String: [URL]] {
        var files = dataset.filesByLabel
        let output = root.appendingPathComponent("jarvis", isDirectory: true)
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: false,
                                                 attributes: [.posixPermissions: 0o700])
        files["jarvis"] = try (dataset.filesByLabel["jarvis"] ?? []).map { source in
            let audio = try AVAudioFile(forReading: source)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: audio.processingFormat,
                                                frameCapacity: AVAudioFrameCount(audio.length)),
                  buffer.format.commonFormat == .pcmFormatFloat32,
                  !buffer.format.isInterleaved else { throw TrainingFailure.invalidAudio }
            try audio.read(into: buffer)
            guard let channels = buffer.floatChannelData else { throw TrainingFailure.invalidAudio }
            let count = Int(buffer.frameLength)
            let step = max(1, Int(buffer.format.sampleRate * 0.032))
            let channelCount = Int(buffer.format.channelCount)
            var rms: [Double] = []
            var offset = 0
            while offset < count {
                let end = min(offset + step, count)
                var energy = 0.0
                for channel in 0 ..< channelCount {
                    for index in offset ..< end {
                        energy += Double(channels[channel][index] * channels[channel][index])
                    }
                }
                rms.append(sqrt(energy / Double((end - offset) * channelCount)))
                offset = end
            }
            guard let range = WakeWordTrainingWindow.sampleRange(
                frameRMS: rms, framesPerRMS: step, sampleCount: count,
                sampleRate: buffer.format.sampleRate
            ), let crop = AVAudioPCMBuffer(pcmFormat: buffer.format,
                                           frameCapacity: AVAudioFrameCount(range.count)),
               let targetChannels = crop.floatChannelData else { throw TrainingFailure.invalidAudio }
            crop.frameLength = AVAudioFrameCount(range.count)
            for channel in 0 ..< channelCount {
                targetChannels[channel].update(from: channels[channel].advanced(by: range.lowerBound),
                                                count: range.count)
            }
            let target = output.appendingPathComponent(source.lastPathComponent)
                .appendingPathExtension("caf")
            let writer = try AVAudioFile(forWriting: target, settings: crop.format.settings)
            try writer.write(from: crop)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
            return target
        }
        return files
    }

    private static func validateCompiledModel(_ url: URL) throws {
        let configuration = MLModelConfiguration()
        configuration.computeUnits = .cpuAndNeuralEngine
        let model = try MLModel(contentsOf: url, configuration: configuration)
        let request = try SNClassifySoundRequest(mlModel: model)
        guard Set(request.knownClassifications) == TrainingDataset.labels else {
            throw TrainingFailure.invalidModel
        }
    }

    private static func validateExistingModel(_ url: URL) throws {
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard
            url.lastPathComponent == "JarvisWakeWord.mlmodelc",
            values.isDirectory == true,
            values.isSymbolicLink != true
        else {
            throw TrainingFailure.invalidModel
        }
        try validateCompiledModel(url)
    }
}
