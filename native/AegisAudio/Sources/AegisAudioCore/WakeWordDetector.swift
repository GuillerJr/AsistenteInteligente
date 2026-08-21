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

public enum WakeWordAvailabilityAction: Equatable, Sendable {
    case none
    case start
    case stop
}

public enum WakeWordAvailabilityPolicy {
    public static func action(
        previousAvailable: Bool,
        currentAvailable: Bool,
        active: Bool,
        startEligible: Bool,
        detectorRunning: Bool
    ) -> WakeWordAvailabilityAction {
        if !currentAvailable {
            return (previousAvailable && active) || detectorRunning ? .stop : .none
        }
        guard
            !previousAvailable,
            startEligible,
            !detectorRunning
        else {
            return .none
        }
        return .start
    }
}

public enum WakeWordThermalPolicy {
    public static func allowsListening(_ state: ProcessInfo.ThermalState) -> Bool {
        switch state {
        case .nominal, .fair:
            true
        case .serious, .critical:
            false
        @unknown default:
            false
        }
    }
}

public enum WakeWordEnergyPolicy {
    public static func allowsListening(lowPowerModeEnabled: Bool) -> Bool {
        !lowPowerModeEnabled
    }
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

public struct WakeWordRecoveryGate: Sendable {
    private var retryAvailable = true

    public init() {}

    public mutating func consumeRetry() -> Bool {
        guard retryAvailable else { return false }
        retryAvailable = false
        return true
    }

    public mutating func reset() {
        retryAvailable = true
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
    private var configurationObserver: (any NSObjectProtocol)?

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
        let configurationObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: engine,
            queue: nil
        ) { _ in
            failureHandler()
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            NotificationCenter.default.removeObserver(configurationObserver)
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
            self.configurationObserver = configurationObserver
        }
    }

    public func stop() {
        let active = lock.withLock { () -> (
            AVAudioEngine,
            SNAudioStreamAnalyzer,
            WakeWordStreamDriver,
            (any NSObjectProtocol)?
        )? in
            guard let engine, let analyzer, let driver else {
                return nil
            }
            self.engine = nil
            self.analyzer = nil
            observer = nil
            self.driver = nil
            let configurationObserver = self.configurationObserver
            self.configurationObserver = nil
            return (engine, analyzer, driver, configurationObserver)
        }
        guard let active else { return }
        if let configurationObserver = active.3 {
            NotificationCenter.default.removeObserver(configurationObserver)
        }
        active.0.stop()
        active.0.inputNode.removeTap(onBus: 0)
        active.1.removeAllRequests()
        active.2.complete()
    }

    deinit {
        stop()
    }
}
