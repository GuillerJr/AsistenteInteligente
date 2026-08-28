import Foundation

public enum WakeWordDetectorError: Error, Equatable, Sendable {
    case permissionRequired(MicrophonePermission)
    case invalidModel
    case invalidInputFormat
    case alreadyRunning
    case thermalUnavailable
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
    let requiredBackgroundMatches: Int
    let maximumMatchGapSeconds: TimeInterval
    let cooldownSeconds: TimeInterval

    private var consecutiveMatches = 0
    private var consecutiveBackgroundMatches = 0
    private var armed = false
    private var lastMatchTime: TimeInterval?
    private var lastObservationTime: TimeInterval?
    private var cooldownUntil: TimeInterval = 0

    init(
        confidenceThreshold: Double = 0.85,
        requiredMatches: Int = 2,
        requiredBackgroundMatches: Int = 2,
        maximumMatchGapSeconds: TimeInterval = 1.5,
        cooldownSeconds: TimeInterval = 5
    ) {
        self.confidenceThreshold = confidenceThreshold
        self.requiredMatches = requiredMatches
        self.requiredBackgroundMatches = requiredBackgroundMatches
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
        guard keywordIsTopClassification else {
            consecutiveMatches = 0
            lastMatchTime = nil
            consecutiveBackgroundMatches += 1
            armed = consecutiveBackgroundMatches >= requiredBackgroundMatches
            return false
        }
        consecutiveBackgroundMatches = 0
        guard armed, confidence >= confidenceThreshold else {
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
        consecutiveBackgroundMatches = 0
        armed = false
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

public typealias WakeWordDetector = AcousticSensor
