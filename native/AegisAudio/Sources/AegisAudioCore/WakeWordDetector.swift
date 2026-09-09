import Foundation

public enum WakeWordDetectorError: Error, Equatable, Sendable {
    case permissionRequired(MicrophonePermission)
    case invalidModel
    case invalidInputFormat
    case alreadyRunning
    case thermalUnavailable
    case speakerIdentityUnavailable
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
        confidenceThreshold: Double = 0.90,
        requiredMatches: Int = 3,
        requiredBackgroundMatches: Int = 3,
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

struct OwnerVerifiedWakeWordGate: Sendable {
    static let minimumOwnerConfidence = 0.78
    static let minimumOwnerMargin = 0.12
    static let requiredOwnerObservations = 2
    static let maximumOwnerObservationGapSeconds: TimeInterval = 1.0
    static let maximumEvidenceSkewSeconds: TimeInterval = 1.25
    static let maximumRecentVoiceAgeSeconds: TimeInterval = 0.75

    let ownerIdentifier: String
    private var wakeWordGate: WakeWordDecisionGate
    private var ownerObservationCount = 0
    private var lastOwnerObservationTime: TimeInterval?
    private var lastSpeakerObservationTime: TimeInterval?
    private var lastVoiceActivityTime: TimeInterval?
    private var pendingWakeWordTime: TimeInterval?

    init(
        ownerIdentifier: String,
        wakeWordGate: WakeWordDecisionGate = WakeWordDecisionGate()
    ) {
        self.ownerIdentifier = ownerIdentifier
        self.wakeWordGate = wakeWordGate
    }

    mutating func observeVoiceActivity(active: Bool, at time: TimeInterval) {
        guard time.isFinite else { return }
        if active {
            lastVoiceActivityTime = time
        }
        discardExpiredEvidence(at: time)
    }

    mutating func observeSpeaker(
        identifier: String,
        confidence: Double,
        runnerUpConfidence: Double,
        at time: TimeInterval
    ) -> Bool {
        guard
            time.isFinite,
            confidence.isFinite,
            runnerUpConfidence.isFinite,
            (0 ... 1).contains(confidence),
            (0 ... 1).contains(runnerUpConfidence),
            lastSpeakerObservationTime.map({ time > $0 }) ?? true
        else {
            return false
        }
        lastSpeakerObservationTime = time

        let isStrongOwnerEvidence = identifier == ownerIdentifier
            && confidence >= Self.minimumOwnerConfidence
            && confidence - runnerUpConfidence >= Self.minimumOwnerMargin
        if isStrongOwnerEvidence {
            if
                let lastOwnerObservationTime,
                time - lastOwnerObservationTime
                    <= Self.maximumOwnerObservationGapSeconds
            {
                ownerObservationCount += 1
            } else {
                ownerObservationCount = 1
            }
            lastOwnerObservationTime = time
        } else {
            ownerObservationCount = 0
            lastOwnerObservationTime = nil
        }
        return consumeActivationIfReady(at: time)
    }

    mutating func observeWakeWord(
        keywordIsTopClassification: Bool,
        confidence: Double,
        at time: TimeInterval
    ) -> Bool {
        if wakeWordGate.observe(
            keywordIsTopClassification: keywordIsTopClassification,
            confidence: confidence,
            at: time
        ) {
            pendingWakeWordTime = time
        }
        return consumeActivationIfReady(at: time)
    }

    private mutating func consumeActivationIfReady(at time: TimeInterval) -> Bool {
        discardExpiredEvidence(at: time)
        guard
            let pendingWakeWordTime,
            let lastOwnerObservationTime,
            let lastVoiceActivityTime,
            ownerObservationCount >= Self.requiredOwnerObservations,
            abs(lastOwnerObservationTime - pendingWakeWordTime)
                <= Self.maximumEvidenceSkewSeconds,
            time - lastVoiceActivityTime <= Self.maximumRecentVoiceAgeSeconds
        else {
            return false
        }

        self.pendingWakeWordTime = nil
        ownerObservationCount = 0
        self.lastOwnerObservationTime = nil
        self.lastVoiceActivityTime = nil
        return true
    }

    private mutating func discardExpiredEvidence(at time: TimeInterval) {
        if
            let pendingWakeWordTime,
            time - pendingWakeWordTime > Self.maximumEvidenceSkewSeconds
        {
            self.pendingWakeWordTime = nil
        }
        if
            let lastOwnerObservationTime,
            time - lastOwnerObservationTime > Self.maximumEvidenceSkewSeconds
        {
            ownerObservationCount = 0
            self.lastOwnerObservationTime = nil
        }
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
