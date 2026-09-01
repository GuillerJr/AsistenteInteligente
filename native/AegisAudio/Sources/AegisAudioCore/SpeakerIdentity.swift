@preconcurrency import AVFoundation
import CoreML
import CryptoKit
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

public struct SpeakerIdentityCapabilitySnapshot: Equatable, Sendable {
    public let state: SpeakerIdentityCapabilityState
    public let speakerIdentifiers: [String]
    public let modelFingerprint: String?
}

public enum SpeakerOwnerPolicy {
    public static func resolvedOwnerIdentifier(
        availableIdentifiers: some Sequence<String>,
        selectedIdentifier: String?
    ) -> String? {
        let available = Set(availableIdentifiers.filter(
            SpeakerIdentityCapability.isValidSpeakerLabel
        ))
        if let selectedIdentifier {
            return available.contains(selectedIdentifier) ? selectedIdentifier : nil
        }
        return available.count == 1 ? available.first : nil
    }

    public static func resolvedOwnerIdentifier(
        availableIdentifiers: some Sequence<String>,
        selectedIdentifier: String?,
        selectedModelFingerprint: String?,
        activeModelFingerprint: String?
    ) -> String? {
        if selectedIdentifier != nil {
            guard
                let selectedModelFingerprint,
                let activeModelFingerprint,
                SpeakerIdentityCapability.isValidModelFingerprint(selectedModelFingerprint),
                selectedModelFingerprint == activeModelFingerprint
            else {
                return nil
            }
        }
        return resolvedOwnerIdentifier(
            availableIdentifiers: availableIdentifiers,
            selectedIdentifier: selectedIdentifier
        )
    }
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
    public static let minimumSpeakerCount = 1
    public static let maximumSpeakerCount = 8

    public static func inspect(bundle: Bundle = .main) -> SpeakerIdentityCapabilityState {
        snapshot(bundle: bundle).state
    }

    public static func snapshot(
        bundle: Bundle = .main
    ) -> SpeakerIdentityCapabilitySnapshot {
        let privateModelURL = SpeakerModelStorage.defaultModelURL()
        if SpeakerModelStorage.assetExists(privateModelURL) {
            guard SpeakerModelStorage.secureModelDirectory(for: privateModelURL) else {
                return SpeakerIdentityCapabilitySnapshot(
                    state: .invalid,
                    speakerIdentifiers: [],
                    modelFingerprint: nil
                )
            }
            return snapshot(modelURL: privateModelURL)
        }
        return snapshot(modelURL: bundledModelURL(bundle: bundle))
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
        snapshot(modelURL: modelURL).state
    }

    public static func snapshot(modelURL: URL?) -> SpeakerIdentityCapabilitySnapshot {
        guard let modelURL else {
            return SpeakerIdentityCapabilitySnapshot(
                state: .missing,
                speakerIdentifiers: [],
                modelFingerprint: nil
            )
        }
        do {
            let request = try validatedRequest(modelURL: modelURL)
            guard let fingerprint = modelFingerprint(at: modelURL) else {
                throw SpeakerIdentityError.invalidModel
            }
            let identifiers = request.knownClassifications
                .filter { $0 != backgroundLabel }
                .sorted()
            return SpeakerIdentityCapabilitySnapshot(
                state: .ready,
                speakerIdentifiers: identifiers,
                modelFingerprint: fingerprint
            )
        } catch {
            return SpeakerIdentityCapabilitySnapshot(
                state: .invalid,
                speakerIdentifiers: [],
                modelFingerprint: nil
            )
        }
    }

    public static func isValidModelFingerprint(_ value: String) -> Bool {
        value.count == 64 && value.unicodeScalars.allSatisfy {
            CharacterSet(charactersIn: "0123456789abcdef").contains($0)
        }
    }

    static func modelFingerprint(
        at modelURL: URL,
        fileManager: FileManager = .default
    ) -> String? {
        let resourceKeys: Set<URLResourceKey> = [
            .isDirectoryKey,
            .isRegularFileKey,
            .isSymbolicLinkKey,
        ]
        var enumerationFailed = false
        guard
            modelURL.pathExtension == modelResourceExtension,
            let rootValues = try? modelURL.resourceValues(forKeys: resourceKeys),
            rootValues.isDirectory == true,
            rootValues.isSymbolicLink != true,
            let enumerator = fileManager.enumerator(
                at: modelURL,
                includingPropertiesForKeys: Array(resourceKeys),
                options: [],
                errorHandler: { _, _ in
                    enumerationFailed = true
                    return false
                }
            )
        else {
            return nil
        }

        let rootPrefix = modelURL.standardizedFileURL.path + "/"
        var files: [(relativePath: String, url: URL)] = []
        while let entry = enumerator.nextObject() as? URL {
            guard let values = try? entry.resourceValues(forKeys: resourceKeys) else {
                return nil
            }
            guard values.isSymbolicLink != true else { return nil }
            if values.isDirectory == true { continue }
            guard values.isRegularFile == true else { return nil }
            let path = entry.standardizedFileURL.path
            guard path.hasPrefix(rootPrefix) else { return nil }
            let relativePath = String(path.dropFirst(rootPrefix.count))
            guard !relativePath.isEmpty else { return nil }
            files.append((relativePath, entry))
        }
        guard !enumerationFailed, !files.isEmpty else { return nil }

        var hasher = SHA256()
        hasher.update(data: Data("jarvis-speaker-model-v1".utf8))
        for file in files.sorted(by: { $0.relativePath < $1.relativePath }) {
            guard let contents = try? Data(contentsOf: file.url, options: .mappedIfSafe) else {
                return nil
            }
            updateFingerprint(&hasher, with: Data(file.relativePath.utf8))
            updateFingerprint(&hasher, with: contents)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func updateFingerprint(_ hasher: inout SHA256, with data: Data) {
        var length = UInt64(data.count).bigEndian
        withUnsafeBytes(of: &length) { bytes in
            hasher.update(data: Data(bytes))
        }
        hasher.update(data: data)
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
    private var gate: SpeakerIdentityDecisionGate
    private var completed = false
    private var failed = false

    init(gate: SpeakerIdentityDecisionGate = SpeakerIdentityDecisionGate()) {
        self.gate = gate
    }

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
    let soleSpeakerIdentifier: String?
    let ownerSpeakerIdentifier: String?

    init(
        format: AVAudioFormat,
        modelURL: URL,
        selectedOwnerIdentifier: String? = nil,
        expectedModelFingerprint: String? = nil,
        confidenceThreshold: Double = 0.78,
        marginThreshold: Double = 0.12,
        requiredObservations: Int = 2
    ) throws {
        guard
            confidenceThreshold.isFinite,
            marginThreshold.isFinite,
            (0 ... 1).contains(confidenceThreshold),
            (0 ... 1).contains(marginThreshold),
            (1 ... 100).contains(requiredObservations)
        else {
            throw SpeakerIdentityError.analysisUnavailable
        }
        let request = try SpeakerIdentityCapability.validatedRequest(modelURL: modelURL)
        guard let modelFingerprint = SpeakerIdentityCapability.modelFingerprint(at: modelURL) else {
            throw SpeakerIdentityError.invalidModel
        }
        let speakers = Set(request.knownClassifications).subtracting([
            SpeakerIdentityCapability.backgroundLabel
        ])
        soleSpeakerIdentifier = speakers.count == 1 ? speakers.first : nil
        let fingerprintMatches = expectedModelFingerprint == nil
            || expectedModelFingerprint == modelFingerprint
        ownerSpeakerIdentifier = fingerprintMatches
            ? SpeakerOwnerPolicy.resolvedOwnerIdentifier(
                availableIdentifiers: speakers,
                selectedIdentifier: selectedOwnerIdentifier
            )
            : nil
        let analyzer = SNAudioStreamAnalyzer(format: format)
        let observer = SpeakerIdentityObserver(
            gate: SpeakerIdentityDecisionGate(
                confidenceThreshold: confidenceThreshold,
                marginThreshold: marginThreshold,
                requiredObservations: requiredObservations
            )
        )
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
