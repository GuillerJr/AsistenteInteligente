@preconcurrency import AVFoundation
import Accelerate
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

public struct AcousticActivityEvent: Equatable, Sendable {
    public let rms: Float
    public let noiseFloor: Float
    public let activeThreshold: Float
    public let voiceActive: Bool
}

public struct AdaptiveNoiseFloorTracker: Sendable {
    public static let thresholdMultiplier: Float = 2.5
    public static let downwardAlpha: Float = 0.05
    public static let upwardAlpha: Float = 0.005
    public static let requiredAttackBuffers = 3
    public static let requiredReleaseBuffers = 8

    public private(set) var noiseFloor: Float
    public private(set) var voiceActive = false
    private var attackCount = 0
    private var releaseCount = 0
    private var sustainedAmbientCount = 0

    public init(initialNoiseFloor: Float = 0.004) {
        noiseFloor = max(0.000_01, initialNoiseFloor.isFinite ? initialNoiseFloor : 0.004)
    }

    public var activeThreshold: Float {
        max(0.000_025, noiseFloor * Self.thresholdMultiplier)
    }

    public mutating func observe(rms rawRMS: Float) -> AcousticActivityEvent? {
        guard rawRMS.isFinite else {
            attackCount = 0
            return nil
        }
        let rms = max(0, rawRMS)
        let previousState = voiceActive
        if rms < noiseFloor {
            noiseFloor = Self.downwardAlpha * rms + (1 - Self.downwardAlpha) * noiseFloor
            sustainedAmbientCount = 0
        } else if !voiceActive, rms < activeThreshold {
            noiseFloor = Self.upwardAlpha * rms + (1 - Self.upwardAlpha) * noiseFloor
            sustainedAmbientCount = 0
        } else if rms <= noiseFloor * 4 {
            sustainedAmbientCount += 1
            if sustainedAmbientCount >= 150 {
                noiseFloor = Self.upwardAlpha * rms + (1 - Self.upwardAlpha) * noiseFloor
            }
        } else {
            sustainedAmbientCount = 0
        }

        if voiceActive {
            if rms < activeThreshold * 0.75 {
                releaseCount += 1
                if releaseCount >= Self.requiredReleaseBuffers {
                    voiceActive = false
                    releaseCount = 0
                    attackCount = 0
                }
            } else {
                releaseCount = 0
            }
        } else if rms > activeThreshold {
            attackCount += 1
            if attackCount >= Self.requiredAttackBuffers {
                voiceActive = true
                attackCount = 0
                releaseCount = 0
            }
        } else {
            attackCount = 0
        }

        guard previousState != voiceActive else { return nil }
        return AcousticActivityEvent(
            rms: rms,
            noiseFloor: noiseFloor,
            activeThreshold: activeThreshold,
            voiceActive: voiceActive
        )
    }
}

private final class AcousticActivationCoordinator: @unchecked Sendable {
    private let lock = NSLock()
    private var gate: OwnerVerifiedWakeWordGate
    private let detectionHandler: @Sendable () -> Void

    init(
        ownerIdentifier: String,
        detectionHandler: @escaping @Sendable () -> Void
    ) {
        gate = OwnerVerifiedWakeWordGate(ownerIdentifier: ownerIdentifier)
        self.detectionHandler = detectionHandler
    }

    func observeVoiceActivity(active: Bool, at time: TimeInterval) {
        lock.withLock {
            gate.observeVoiceActivity(active: active, at: time)
        }
    }

    func observeWakeWord(
        keywordIsTopClassification: Bool,
        confidence: Double,
        at time: TimeInterval
    ) {
        let accepted = lock.withLock {
            gate.observeWakeWord(
                keywordIsTopClassification: keywordIsTopClassification,
                confidence: confidence,
                at: time
            )
        }
        if accepted { detectionHandler() }
    }

    func observeSpeaker(
        identifier: String,
        confidence: Double,
        runnerUpConfidence: Double,
        at time: TimeInterval
    ) {
        let accepted = lock.withLock {
            gate.observeSpeaker(
                identifier: identifier,
                confidence: confidence,
                runnerUpConfidence: runnerUpConfidence,
                at: time
            )
        }
        if accepted { detectionHandler() }
    }
}

private final class AcousticResultsObserver: NSObject, SNResultsObserving, @unchecked Sendable {
    private let coordinator: AcousticActivationCoordinator
    private let failureHandler: @Sendable () -> Void

    init(
        coordinator: AcousticActivationCoordinator,
        failureHandler: @escaping @Sendable () -> Void
    ) {
        self.coordinator = coordinator
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
        coordinator.observeWakeWord(
            keywordIsTopClassification:
                result.classifications.first?.identifier == WakeWordCapability.keywordLabel,
            confidence: keyword.confidence,
            at: ProcessInfo.processInfo.systemUptime
        )
    }

    func request(_ request: any SNRequest, didFailWithError error: any Error) {
        failureHandler()
    }
}

private final class AcousticSpeakerResultsObserver: NSObject, SNResultsObserving,
    @unchecked Sendable
{
    private let coordinator: AcousticActivationCoordinator
    private let failureHandler: @Sendable () -> Void

    init(
        coordinator: AcousticActivationCoordinator,
        failureHandler: @escaping @Sendable () -> Void
    ) {
        self.coordinator = coordinator
        self.failureHandler = failureHandler
    }

    func request(_ request: any SNRequest, didProduce result: any SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        let ordered = result.classifications.sorted { $0.confidence > $1.confidence }
        guard let top = ordered.first else { return }
        coordinator.observeSpeaker(
            identifier: top.identifier,
            confidence: top.confidence,
            runnerUpConfidence: ordered.dropFirst().first?.confidence ?? 0,
            at: ProcessInfo.processInfo.systemUptime
        )
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
    private var noiseTracker = AdaptiveNoiseFloorTracker()
    private let activationCoordinator: AcousticActivationCoordinator
    private let activityHandler: @Sendable (AcousticActivityEvent) -> Void

    init(
        analyzer: SNAudioStreamAnalyzer,
        activationCoordinator: AcousticActivationCoordinator,
        activityHandler: @escaping @Sendable (AcousticActivityEvent) -> Void
    ) {
        self.analyzer = analyzer
        self.activationCoordinator = activationCoordinator
        self.activityHandler = activityHandler
    }

    func analyze(_ buffer: AVAudioPCMBuffer) {
        var rms: Float = 0
        if let channel = buffer.floatChannelData?.pointee, buffer.frameLength > 0 {
            vDSP_rmsqv(channel, 1, &rms, vDSP_Length(buffer.frameLength))
        }
        let observation = lock.withLock { () -> (AcousticActivityEvent?, Bool)? in
            guard !completed else { return nil }
            analyzer.analyze(buffer, atAudioFramePosition: framePosition)
            framePosition += AVAudioFramePosition(buffer.frameLength)
            let transition = noiseTracker.observe(rms: rms)
            return (transition, noiseTracker.voiceActive)
        }
        guard let observation else { return }
        activationCoordinator.observeVoiceActivity(
            active: observation.1,
            at: ProcessInfo.processInfo.systemUptime
        )
        if let transition = observation.0 { activityHandler(transition) }
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
    private static let analysisBufferFrames: AVAudioFrameCount = 1_024
    private let modelURL: URL?
    private let processInfo: ProcessInfo
    private let lock = NSLock()
    private var starting = false
    private var thermalListeningAllowed: Bool
    private var engine: AVAudioEngine?
    private var analyzer: SNAudioStreamAnalyzer?
    private var observer: AcousticResultsObserver?
    private var speakerObserver: AcousticSpeakerResultsObserver?
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
        selectedOwnerIdentifier: String,
        expectedSpeakerModelFingerprint: String,
        speakerModelURL: URL? = SpeakerIdentityCapability.modelURL(),
        detectionHandler: @escaping @Sendable () -> Void,
        failureHandler: @escaping @Sendable () -> Void,
        activityHandler: @escaping @Sendable (AcousticActivityEvent) -> Void = { _ in }
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
        guard
            SpeakerIdentityCapability.isValidSpeakerLabel(selectedOwnerIdentifier),
            SpeakerIdentityCapability.isValidModelFingerprint(
                expectedSpeakerModelFingerprint
            ),
            let speakerModelURL,
            SpeakerIdentityCapability.modelFingerprint(at: speakerModelURL)
                == expectedSpeakerModelFingerprint
        else {
            throw WakeWordDetectorError.speakerIdentityUnavailable
        }
        let speakerRequest: SNClassifySoundRequest
        do {
            speakerRequest = try SpeakerIdentityCapability.validatedRequest(
                modelURL: speakerModelURL
            )
            guard speakerRequest.knownClassifications.contains(selectedOwnerIdentifier) else {
                throw SpeakerIdentityError.invalidModel
            }
        } catch {
            throw WakeWordDetectorError.speakerIdentityUnavailable
        }

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate >= 8_000, format.channelCount > 0 else {
            throw WakeWordDetectorError.invalidInputFormat
        }
        let analyzer = SNAudioStreamAnalyzer(format: format)
        let activationCoordinator = AcousticActivationCoordinator(
            ownerIdentifier: selectedOwnerIdentifier,
            detectionHandler: detectionHandler
        )
        let observer = AcousticResultsObserver(
            coordinator: activationCoordinator,
            failureHandler: failureHandler
        )
        let speakerObserver = AcousticSpeakerResultsObserver(
            coordinator: activationCoordinator,
            failureHandler: failureHandler
        )
        do {
            try analyzer.add(request, withObserver: observer)
            try analyzer.add(speakerRequest, withObserver: speakerObserver)
        } catch {
            analyzer.removeAllRequests()
            throw WakeWordDetectorError.analysisUnavailable
        }
        let driver = AcousticStreamDriver(
            analyzer: analyzer,
            activationCoordinator: activationCoordinator,
            activityHandler: activityHandler
        )
        input.installTap(
            onBus: 0,
            bufferSize: Self.analysisBufferFrames,
            format: format
        ) { buffer, _ in
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
            self.speakerObserver = speakerObserver
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
            speakerObserver = nil
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
