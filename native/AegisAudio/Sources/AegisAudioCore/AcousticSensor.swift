@preconcurrency import AVFoundation
import Foundation
@preconcurrency import SoundAnalysis

public struct AcousticThermalEvent: Equatable, Sendable {
    public let state: ProcessInfo.ThermalState
    public let allowsListening: Bool

    public init(state: ProcessInfo.ThermalState) {
        self.state = state
        allowsListening = WakeWordThermalPolicy.allowsListening(state)
    }

    public var label: String {
        switch state {
        case .nominal: "nominal"
        case .fair: "fair"
        case .serious: "serious"
        case .critical: "critical"
        @unknown default: "unknown"
        }
    }
}

private final class AcousticResultsObserver: NSObject, SNResultsObserving, @unchecked Sendable {
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

private final class AcousticStreamDriver: @unchecked Sendable {
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

public final class AcousticSensor: @unchecked Sendable {
    private let modelURL: URL?
    private let processInfo: ProcessInfo
    private let lock = NSLock()
    private var starting = false
    private var thermalListeningAllowed: Bool
    private var engine: AVAudioEngine?
    private var analyzer: SNAudioStreamAnalyzer?
    private var observer: AcousticResultsObserver?
    private var driver: AcousticStreamDriver?
    private var configurationObserver: (any NSObjectProtocol)?
    private var thermalObserver: (any NSObjectProtocol)?
    private var thermalHandler: (@Sendable (AcousticThermalEvent) -> Void)?

    public convenience init(bundle: Bundle = .main) {
        self.init(
            modelURL: bundle.url(
                forResource: WakeWordCapability.modelResourceName,
                withExtension: WakeWordCapability.modelResourceExtension
            )
        )
    }

    init(modelURL: URL?, processInfo: ProcessInfo = .processInfo) {
        self.modelURL = modelURL
        self.processInfo = processInfo
        thermalListeningAllowed = WakeWordThermalPolicy.allowsListening(processInfo.thermalState)
    }

    public var isRunning: Bool {
        lock.withLock { engine != nil }
    }

    public func monitorThermalState(
        _ handler: @escaping @Sendable (AcousticThermalEvent) -> Void
    ) {
        _ = processInfo.thermalState
        let shouldInstall = lock.withLock {
            thermalHandler = handler
            return thermalObserver == nil
        }
        if shouldInstall {
            let token = NotificationCenter.default.addObserver(
                forName: ProcessInfo.thermalStateDidChangeNotification,
                object: processInfo,
                queue: nil
            ) { [weak self] _ in
                self?.handleThermalTransition()
            }
            lock.withLock {
                thermalObserver = token
            }
        }
        handleThermalTransition()
    }

    public func start(
        detectionHandler: @escaping @Sendable () -> Void,
        failureHandler: @escaping @Sendable () -> Void
    ) throws {
        let mayStart = lock.withLock {
            guard engine == nil, !starting, thermalListeningAllowed else { return false }
            starting = true
            return true
        }
        guard mayStart else {
            if !WakeWordThermalPolicy.allowsListening(processInfo.thermalState) {
                throw WakeWordDetectorError.thermalUnavailable
            }
            throw WakeWordDetectorError.alreadyRunning
        }
        defer { lock.withLock { starting = false } }
        guard WakeWordThermalPolicy.allowsListening(processInfo.thermalState) else {
            lock.withLock { thermalListeningAllowed = false }
            throw WakeWordDetectorError.thermalUnavailable
        }
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
        let observer = AcousticResultsObserver(
            detectionHandler: detectionHandler,
            failureHandler: failureHandler
        )
        do {
            try analyzer.add(request, withObserver: observer)
        } catch {
            throw WakeWordDetectorError.analysisUnavailable
        }
        let driver = AcousticStreamDriver(analyzer: analyzer)
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
            Self.release(
                engine: engine,
                analyzer: analyzer,
                driver: driver,
                configurationObserver: configurationObserver
            )
            throw WakeWordDetectorError.engineFailed
        }

        let finalThermalAllowed = WakeWordThermalPolicy.allowsListening(processInfo.thermalState)
        let accepted = lock.withLock {
            thermalListeningAllowed = finalThermalAllowed
            guard finalThermalAllowed else { return false }
            self.engine = engine
            self.analyzer = analyzer
            self.observer = observer
            self.driver = driver
            self.configurationObserver = configurationObserver
            return true
        }
        guard accepted else {
            Self.release(
                engine: engine,
                analyzer: analyzer,
                driver: driver,
                configurationObserver: configurationObserver
            )
            throw WakeWordDetectorError.thermalUnavailable
        }
    }

    public func stop() {
        let active = lock.withLock { () -> (
            AVAudioEngine,
            SNAudioStreamAnalyzer,
            AcousticStreamDriver,
            (any NSObjectProtocol)?
        )? in
            guard let engine, let analyzer, let driver else { return nil }
            self.engine = nil
            self.analyzer = nil
            observer = nil
            self.driver = nil
            let configurationObserver = self.configurationObserver
            self.configurationObserver = nil
            return (engine, analyzer, driver, configurationObserver)
        }
        guard let active else { return }
        Self.release(
            engine: active.0,
            analyzer: active.1,
            driver: active.2,
            configurationObserver: active.3
        )
    }

    private func handleThermalTransition() {
        let event = AcousticThermalEvent(state: processInfo.thermalState)
        let handler = lock.withLock {
            thermalListeningAllowed = event.allowsListening
            return thermalHandler
        }
        if !event.allowsListening {
            stop()
        }
        handler?(event)
    }

    private static func release(
        engine: AVAudioEngine,
        analyzer: SNAudioStreamAnalyzer,
        driver: AcousticStreamDriver,
        configurationObserver: (any NSObjectProtocol)?
    ) {
        if let configurationObserver {
            NotificationCenter.default.removeObserver(configurationObserver)
        }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        analyzer.removeAllRequests()
        driver.complete()
        engine.reset()
    }

    deinit {
        if let thermalObserver = lock.withLock({ self.thermalObserver }) {
            NotificationCenter.default.removeObserver(thermalObserver)
        }
        stop()
    }
}
