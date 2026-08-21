@preconcurrency import AVFoundation
import Foundation
@preconcurrency import SoundAnalysis

public enum WakeWordDetectorError: Error, Equatable, Sendable {
    case permissionRequired(MicrophonePermission)
    case invalidModel
    case invalidInputFormat
    case alreadyRunning
    case analysisUnavailable
    case engineFailed
}

struct WakeWordDecisionGate: Sendable {
    let confidenceThreshold: Double
    let requiredMatches: Int
    let maximumMatchGapSeconds: TimeInterval
    let cooldownSeconds: TimeInterval

    private var consecutiveMatches = 0
    private var lastMatchTime: TimeInterval?
    private var lastObservationTime: TimeInterval?
    private var cooldownUntil: TimeInterval = 0

    init(
        confidenceThreshold: Double = 0.85,
        requiredMatches: Int = 2,
        maximumMatchGapSeconds: TimeInterval = 1.5,
        cooldownSeconds: TimeInterval = 5
    ) {
        self.confidenceThreshold = confidenceThreshold
        self.requiredMatches = requiredMatches
        self.maximumMatchGapSeconds = maximumMatchGapSeconds
        self.cooldownSeconds = cooldownSeconds
    }

    mutating func observe(
        keywordIsTopClassification: Bool,
        confidence: Double,
        at time: TimeInterval
    ) -> Bool {
        guard
            time.isFinite,
            confidence.isFinite,
            (0 ... 1).contains(confidence),
            lastObservationTime.map({ time > $0 }) ?? true
        else {
            return false
        }
        lastObservationTime = time
        guard time >= cooldownUntil else {
            return false
        }
        guard keywordIsTopClassification, confidence >= confidenceThreshold else {
            consecutiveMatches = 0
            lastMatchTime = nil
            return false
        }

        if let lastMatchTime, time - lastMatchTime <= maximumMatchGapSeconds {
            consecutiveMatches += 1
        } else {
            consecutiveMatches = 1
        }
        lastMatchTime = time
        guard consecutiveMatches >= requiredMatches else {
            return false
        }

        consecutiveMatches = 0
        self.lastMatchTime = nil
        cooldownUntil = time + cooldownSeconds
        return true
    }
}

public struct WakeWordResumeGate: Sendable {
    private static let settleSeconds: TimeInterval = 0.75

    private var quietSince: TimeInterval?
    private var lastObservationTime: TimeInterval?

    public init() {}

    public mutating func observe(audioIsBusy: Bool, at time: TimeInterval) -> Bool {
        guard
            time.isFinite,
            lastObservationTime.map({ time > $0 }) ?? true
        else {
            return false
        }
        lastObservationTime = time
        guard !audioIsBusy else {
            quietSince = nil
            return false
        }
        guard let quietSince else {
            self.quietSince = time
            return false
        }
        return time - quietSince >= Self.settleSeconds
    }
}

private final class WakeWordResultsObserver: NSObject, SNResultsObserving, @unchecked Sendable {
    private let lock = NSLock()
    private var gate = WakeWordDecisionGate()
    private let detectionHandler: @Sendable () -> Void
    private let failureHandler: @Sendable () -> Void

    init(
        detectionHandler: @escaping @Sendable () -> Void,
        failureHandler: @escaping @Sendable () -> Void
    ) {
        self.detectionHandler = detectionHandler
        self.failureHandler = failureHandler
    }

    func request(_ request: any SNRequest, didProduce result: any SNResult) {
        guard
            let result = result as? SNClassificationResult,
            let keyword = result.classifications.first(where: {
                $0.identifier == WakeWordCapability.keywordLabel
            })
        else {
            return
        }
        let detected = lock.withLock {
            gate.observe(
                keywordIsTopClassification:
                    result.classifications.first?.identifier == WakeWordCapability.keywordLabel,
                confidence: keyword.confidence,
                at: ProcessInfo.processInfo.systemUptime
            )
        }
        if detected {
            detectionHandler()
        }
    }

    func request(_ request: any SNRequest, didFailWithError error: any Error) {
        failureHandler()
    }
}

private final class WakeWordStreamDriver: @unchecked Sendable {
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

public final class WakeWordDetector: @unchecked Sendable {
    private let modelURL: URL?
    private let lock = NSLock()
    private var starting = false
    private var engine: AVAudioEngine?
    private var analyzer: SNAudioStreamAnalyzer?
    private var observer: WakeWordResultsObserver?
    private var driver: WakeWordStreamDriver?

    public init(bundle: Bundle = .main) {
        modelURL = bundle.url(
            forResource: WakeWordCapability.modelResourceName,
            withExtension: WakeWordCapability.modelResourceExtension
        )
    }

    init(modelURL: URL?) {
        self.modelURL = modelURL
    }

    public var isRunning: Bool {
        lock.withLock { engine != nil }
    }

    public func start(
        detectionHandler: @escaping @Sendable () -> Void,
        failureHandler: @escaping @Sendable () -> Void
    ) throws {
        let mayStart = lock.withLock {
            guard engine == nil, !starting else { return false }
            starting = true
            return true
        }
        guard mayStart else {
            throw WakeWordDetectorError.alreadyRunning
        }
        defer { lock.withLock { starting = false } }
        let permission = MicrophonePermission.current
        guard permission == .authorized else {
            throw WakeWordDetectorError.permissionRequired(permission)
        }
        guard let modelURL else {
            throw WakeWordDetectorError.invalidModel
        }
        let request: SNClassifySoundRequest
        do {
            request = try WakeWordCapability.validatedRequest(modelURL: modelURL)
        } catch {
            throw WakeWordDetectorError.invalidModel
        }

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate >= 8_000, format.channelCount > 0 else {
            throw WakeWordDetectorError.invalidInputFormat
        }
        let analyzer = SNAudioStreamAnalyzer(format: format)
        let observer = WakeWordResultsObserver(
            detectionHandler: detectionHandler,
            failureHandler: failureHandler
        )
        do {
            try analyzer.add(request, withObserver: observer)
        } catch {
            throw WakeWordDetectorError.analysisUnavailable
        }
        let driver = WakeWordStreamDriver(analyzer: analyzer)
        input.installTap(onBus: 0, bufferSize: 4_096, format: format) { buffer, _ in
            driver.analyze(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            analyzer.removeAllRequests()
            driver.complete()
            throw WakeWordDetectorError.engineFailed
        }

        lock.withLock {
            self.engine = engine
            self.analyzer = analyzer
            self.observer = observer
            self.driver = driver
        }
    }

    public func stop() {
        let active = lock.withLock { () -> (
            AVAudioEngine,
            SNAudioStreamAnalyzer,
            WakeWordStreamDriver
        )? in
            guard let engine, let analyzer, let driver else {
                return nil
            }
            self.engine = nil
            self.analyzer = nil
            observer = nil
            self.driver = nil
            return (engine, analyzer, driver)
        }
        guard let active else { return }
        active.0.stop()
        active.0.inputNode.removeTap(onBus: 0)
        active.1.removeAllRequests()
        active.2.complete()
    }

    deinit {
        stop()
    }
}
