import CoreML
import Foundation
import SoundAnalysis

public enum WakeWordCapabilityState: String, Equatable, Sendable {
    case missing
    case invalid
    case ready
}

public enum WakeWordCapability {
    public static let modelResourceName = "JarvisWakeWord"
    public static let modelResourceExtension = "mlmodelc"
    public static let keywordLabel = "jarvis"
    public static let maximumClassificationCount = 16

    public static func inspect(bundle: Bundle = .main) -> WakeWordCapabilityState {
        inspect(
            modelURL: bundle.url(
                forResource: modelResourceName,
                withExtension: modelResourceExtension
            )
        )
    }

    public static func inspect(modelURL: URL?) -> WakeWordCapabilityState {
        guard let modelURL else {
            return .missing
        }
        do {
            _ = try validatedRequest(modelURL: modelURL)
            return .ready
        } catch {
            return .invalid
        }
    }

    static func validatedRequest(modelURL: URL) throws -> SNClassifySoundRequest {
        let values = try modelURL.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard
            modelURL.pathExtension == modelResourceExtension,
            values.isDirectory == true,
            values.isSymbolicLink != true
        else {
            throw WakeWordDetectorError.invalidModel
        }

        let configuration = MLModelConfiguration()
        configuration.computeUnits = .cpuAndNeuralEngine
        let model = try MLModel(contentsOf: modelURL, configuration: configuration)
        let request = try SNClassifySoundRequest(mlModel: model)
        guard
            request.knownClassifications.contains(keywordLabel),
            request.knownClassifications.count <= maximumClassificationCount
        else {
            throw WakeWordDetectorError.invalidModel
        }
        request.overlapFactor = 0.5
        return request
    }
}
