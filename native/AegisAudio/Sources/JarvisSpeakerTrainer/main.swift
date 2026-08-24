import AVFAudio
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

private struct SpeakerDataset {
    static let backgroundLabel = "background"
    static let audioExtensions: Set<String> = ["aif", "aiff", "caf", "wav"]
    static let minimumSpeakers = 2
    static let maximumSpeakers = 8
    static let minimumSamplesPerLabel = 20
    static let maximumSamplesPerLabel = 500
    static let maximumFileBytes = 5 * 1_024 * 1_024
    static let maximumDatasetBytes = 1_024 * 1_024 * 1_024

    let filesByLabel: [String: [URL]]
    let fingerprint: String

    var labels: Set<String> { Set(filesByLabel.keys) }
    var speakerLabels: [String] { labels.subtracting([Self.backgroundLabel]).sorted() }

    static func inspect(root: URL) throws -> SpeakerDataset {
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
        let labels = Set(entries.map(\.lastPathComponent))
        let speakers = labels.subtracting([backgroundLabel])
        guard
            labels.contains(backgroundLabel),
            (minimumSpeakers ... maximumSpeakers).contains(speakers.count),
            labels.count == speakers.count + 1,
            speakers.allSatisfy(validSpeakerLabel)
        else {
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
        return SpeakerDataset(filesByLabel: filesByLabel, fingerprint: fingerprint)
    }

    static func validSpeakerLabel(_ value: String) -> Bool {
        guard (2 ... 32).contains(value.count), value != backgroundLabel else { return false }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789_-")
        let firstAllowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789")
        guard
            value.unicodeScalars.allSatisfy(allowed.contains),
            let first = value.unicodeScalars.first
        else {
            return false
        }
        return firstAllowed.contains(first)
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
            (0.8 ... 8).contains(duration)
        else {
            throw TrainingFailure.invalidAudio
        }
        return size
    }
}

@main
private enum SpeakerTrainer {
    static func main() async {
        do {
            let arguments = CommandLine.arguments
            guard arguments.count == 3 else {
                throw TrainingFailure.invalidArguments
            }
            if arguments[1] == "--check" {
                let dataset = try SpeakerDataset.inspect(
                    root: URL(fileURLWithPath: arguments[2]).standardizedFileURL
                )
                print(
                    "status=ready speakers=\(dataset.speakerLabels.joined(separator: ",")) "
                        + "labels=\(dataset.labels.count)"
                )
                return
            }
            if arguments[1] == "--validate-model" {
                try validateExistingModel(
                    URL(fileURLWithPath: arguments[2]).standardizedFileURL
                )
                print("status=valid model=JarvisSpeakerIdentity.mlmodelc")
                return
            }
            let datasetURL = URL(fileURLWithPath: arguments[1]).standardizedFileURL
            let outputURL = URL(fileURLWithPath: arguments[2]).standardizedFileURL
            try validateOutput(outputURL)
            let dataset = try SpeakerDataset.inspect(root: datasetURL)
            print(
                "status=training speakers=\(dataset.speakerLabels.joined(separator: ",")) "
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
                trainingData: .filesByLabel(dataset.filesByLabel),
                parameters: parameters
            )
            guard
                classifier.trainingMetrics.isValid,
                classifier.validationMetrics.isValid,
                classifier.validationMetrics.classificationError <= 0.25
            else {
                throw TrainingFailure.poorValidation
            }

            let manager = FileManager.default
            let temporaryRoot = manager.temporaryDirectory.appending(
                path: "jarvis-speaker-training-\(UUID().uuidString)",
                directoryHint: .isDirectory
            )
            try manager.createDirectory(at: temporaryRoot, withIntermediateDirectories: false)
            defer { try? manager.removeItem(at: temporaryRoot) }
            let sourceModel = temporaryRoot.appending(path: "JarvisSpeakerIdentity.mlmodel")
            try classifier.write(
                to: sourceModel,
                metadata: MLModelMetadata(
                    author: "Jarvis",
                    shortDescription: "Local speaker identity classifier for Jarvis",
                    license: "Private local model",
                    version: "1",
                    additional: [
                        "dataset_sha256": dataset.fingerprint,
                        "labels": dataset.labels.sorted().joined(separator: ","),
                    ]
                )
            )
            let compiledModel = try await MLModel.compileModel(at: sourceModel)
            try validateCompiledModel(compiledModel, expectedLabels: dataset.labels)
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
            outputURL.lastPathComponent == "JarvisSpeakerIdentity.mlmodelc",
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

    private static func validateCompiledModel(_ url: URL, expectedLabels: Set<String>?) throws {
        let configuration = MLModelConfiguration()
        configuration.computeUnits = .cpuAndNeuralEngine
        let model = try MLModel(contentsOf: url, configuration: configuration)
        let request = try SNClassifySoundRequest(mlModel: model)
        let labels = Set(request.knownClassifications)
        let speakers = labels.subtracting([SpeakerDataset.backgroundLabel])
        guard
            labels.contains(SpeakerDataset.backgroundLabel),
            (SpeakerDataset.minimumSpeakers ... SpeakerDataset.maximumSpeakers)
                .contains(speakers.count),
            labels.count == speakers.count + 1,
            speakers.allSatisfy(SpeakerDataset.validSpeakerLabel),
            expectedLabels.map({ $0 == labels }) ?? true
        else {
            throw TrainingFailure.invalidModel
        }
    }

    private static func validateExistingModel(_ url: URL) throws {
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard
            url.lastPathComponent == "JarvisSpeakerIdentity.mlmodelc",
            values.isDirectory == true,
            values.isSymbolicLink != true
        else {
            throw TrainingFailure.invalidModel
        }
        try validateCompiledModel(url, expectedLabels: nil)
    }
}
