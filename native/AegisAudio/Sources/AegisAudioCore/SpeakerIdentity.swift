@preconcurrency import AVFoundation
import CoreML
import Foundation
@preconcurrency import SoundAnalysis

public enum SpeakerIdentityCapabilityState: String, Equatable, Sendable {
    case missing
    case invalid
    case ready
}

public enum SpeakerIdentityError: Error, Equatable, Sendable {
    case invalidModel
    case analysisUnavailable
}

public struct SpeakerIdentityResult: Equatable, Sendable {
    public let identifier: String
    public let confidence: Double

    init?(identifier: String, confidence: Double) {
        guard
            SpeakerIdentityCapability.isValidSpeakerLabel(identifier),
            confidence.isFinite,
            (0 ... 1).contains(confidence)
        else {
            return nil
        }
        self.identifier = identifier
        self.confidence = confidence
    }
}

public enum SpeakerIdentityCapability {
    public static let modelResourceName = "JarvisSpeakerIdentity"
    public static let modelResourceExtension = "mlmodelc"
    public static let backgroundLabel = "background"
    public static let minimumSpeakerCount = 2
    public static let maximumSpeakerCount = 8

    public static func inspect(bundle: Bundle = .main) -> SpeakerIdentityCapabilityState {
        let privateModelURL = SpeakerModelStorage.defaultModelURL()
        if SpeakerModelStorage.assetExists(privateModelURL) {
            guard SpeakerModelStorage.secureModelDirectory(for: privateModelURL) else {
                return .invalid
            }
            return inspect(modelURL: privateModelURL)
        }
        return inspect(modelURL: bundledModelURL(bundle: bundle))
    }

    public static func modelURL(bundle: Bundle = .main) -> URL? {
        let privateModelURL = SpeakerModelStorage.defaultModelURL()
        if SpeakerModelStorage.assetExists(privateModelURL) {
            return SpeakerModelStorage.secureModelDirectory(for: privateModelURL)
                ? privateModelURL
                : nil
        }
        return bundledModelURL(bundle: bundle)
    }

    public static func inspect(modelURL: URL?) -> SpeakerIdentityCapabilityState {
        guard let modelURL else { return .missing }
        do {
            _ = try validatedRequest(modelURL: modelURL)
            return .ready
        } catch {
            return .invalid
        }
    }

    public static func isValidSpeakerLabel(_ value: String) -> Bool {
        guard (2 ... 32).contains(value.count), value != backgroundLabel else {
            return false
        }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789_-")
        guard
            value.unicodeScalars.allSatisfy(allowed.contains),
            let first = value.unicodeScalars.first
        else {
            return false
        }
        return CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789").contains(first)
    }

    private static func bundledModelURL(bundle: Bundle) -> URL? {
        bundle.url(
            forResource: modelResourceName,
            withExtension: modelResourceExtension
        )
    }

    static func validatedRequest(modelURL: URL) throws -> SNClassifySoundRequest {
        let values = try modelURL.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard
            modelURL.pathExtension == modelResourceExtension,
            values.isDirectory == true,
            values.isSymbolicLink != true
        else {
            throw SpeakerIdentityError.invalidModel
        }

        let configuration = MLModelConfiguration()
        configuration.computeUnits = .cpuAndNeuralEngine
        let model = try MLModel(contentsOf: modelURL, configuration: configuration)
        let request = try SNClassifySoundRequest(mlModel: model)
        let labels = Set(request.knownClassifications)
        let speakerLabels = labels.subtracting([backgroundLabel])
        guard
            labels.contains(backgroundLabel),
            (minimumSpeakerCount ... maximumSpeakerCount).contains(speakerLabels.count),
            labels.count == speakerLabels.count + 1,
            speakerLabels.allSatisfy(isValidSpeakerLabel)
        else {
            throw SpeakerIdentityError.invalidModel
        }
        request.overlapFactor = 0.5
        return request
    }
}

struct SpeakerIdentityDecisionGate: Sendable {
    private struct Score: Sendable {
        var confidenceTotal = 0.0
        var marginTotal = 0.0
        var count = 0
    }

    let confidenceThreshold: Double
    let marginThreshold: Double
    let requiredObservations: Int
    private var scores: [String: Score] = [:]

    init(
        confidenceThreshold: Double = 0.78,
        marginThreshold: Double = 0.12,
        requiredObservations: Int = 2
    ) {
        self.confidenceThreshold = confidenceThreshold
        self.marginThreshold = marginThreshold
        self.requiredObservations = requiredObservations
    }

    mutating func observe(
        identifier: String,
        confidence: Double,
        runnerUpConfidence: Double
    ) {
        guard
            SpeakerIdentityCapability.isValidSpeakerLabel(identifier),
            confidence.isFinite,
            runnerUpConfidence.isFinite,
            (0 ... 1).contains(confidence),
            (0 ... 1).contains(runnerUpConfidence)
        else {
            return
        }
        var score = scores[identifier, default: Score()]
        score.confidenceTotal += confidence
        score.marginTotal += max(0, confidence - runnerUpConfidence)
        score.count += 1
        scores[identifier] = score
    }

    func result() -> SpeakerIdentityResult? {
        let candidates = scores.compactMap { identifier, score -> SpeakerIdentityResult? in
            guard score.count >= requiredObservations else { return nil }
            let confidence = score.confidenceTotal / Double(score.count)
            let margin = score.marginTotal / Double(score.count)
            guard confidence >= confidenceThreshold, margin >= marginThreshold else { return nil }
            return SpeakerIdentityResult(identifier: identifier, confidence: confidence)
        }
        return candidates.max { left, right in left.confidence < right.confidence }
    }
}

private final class SpeakerIdentityObserver: NSObject, SNResultsObserving, @unchecked Sendable {
    private let lock = NSLock()
    private let completion = DispatchSemaphore(value: 0)
    private var gate = SpeakerIdentityDecisionGate()
    private var completed = false
    private var failed = false

    func request(_ request: any SNRequest, didProduce result: any SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        let ordered = result.classifications.sorted { $0.confidence > $1.confidence }
        guard
            let top = ordered.first,
            top.identifier != SpeakerIdentityCapability.backgroundLabel
        else {
            return
        }
        let runnerUp = ordered.dropFirst().first?.confidence ?? 0
        lock.withLock {
            gate.observe(
                identifier: top.identifier,
                confidence: top.confidence,
                runnerUpConfidence: runnerUp
            )
        }
    }

    func request(_ request: any SNRequest, didFailWithError error: any Error) {
        lock.withLock { failed = true }
        signalCompletion()
    }

    func requestDidComplete(_ request: any SNRequest) {
        signalCompletion()
    }

    func waitForResult(timeoutSeconds: Double) -> SpeakerIdentityResult? {
        guard completion.wait(timeout: .now() + timeoutSeconds) == .success else {
            return nil
        }
        return lock.withLock { failed ? nil : gate.result() }
    }

    private func signalCompletion() {
        let shouldSignal = lock.withLock {
            guard !completed else { return false }
            completed = true
            return true
        }
        if shouldSignal { completion.signal() }
    }
}

private final class SpeakerIdentityDriver: @unchecked Sendable {
    private let analyzer: SNAudioStreamAnalyzer
    private let lock = NSLock()
    private var framePosition: AVAudioFramePosition = 0
    private var completed = false

    init(analyzer: SNAudioStreamAnalyzer) {
        self.analyzer = analyzer
    }

    func analyze(_ buffer: AVAudioPCMBuffer) {
        lock.withLock {
            guard !completed else { return }
            analyzer.analyze(buffer, atAudioFramePosition: framePosition)
            framePosition += AVAudioFramePosition(buffer.frameLength)
        }
    }

    func complete() {
        lock.withLock {
            guard !completed else { return }
            completed = true
            analyzer.completeAnalysis()
        }
    }
}

final class SpeakerIdentitySession: @unchecked Sendable {
    private let analyzer: SNAudioStreamAnalyzer
    private let observer: SpeakerIdentityObserver
    private let driver: SpeakerIdentityDriver

    init(format: AVAudioFormat, modelURL: URL) throws {
        let request = try SpeakerIdentityCapability.validatedRequest(modelURL: modelURL)
        let analyzer = SNAudioStreamAnalyzer(format: format)
        let observer = SpeakerIdentityObserver()
        do {
            try analyzer.add(request, withObserver: observer)
        } catch {
            throw SpeakerIdentityError.analysisUnavailable
        }
        self.analyzer = analyzer
        self.observer = observer
        driver = SpeakerIdentityDriver(analyzer: analyzer)
    }

    func analyze(_ buffer: AVAudioPCMBuffer) {
        driver.analyze(buffer)
    }

    func finish(timeoutSeconds: Double) -> SpeakerIdentityResult? {
        driver.complete()
        let result = observer.waitForResult(timeoutSeconds: timeoutSeconds)
        analyzer.removeAllRequests()
        return result
    }
}
