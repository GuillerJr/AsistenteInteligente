import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech

private let voiceConversationDefaultsKey = "ai.aegis.voice.conversation-id"
private let wakeWordOptInDefaultsKey = "ai.aegis.voice.wake-word-enabled"
private let screenCaptureRequestDefaultsKey = "ai.aegis.privacy.screen-requested"
private let systemSettingsBundleIdentifier = "com.apple.systempreferences"

private enum PrivacyRefreshTarget {
    case microphone
    case speech
    case screenCapture
    case computerControl
}

enum DaemonConnectionState: Sendable {
    case unknown
    case checking
    case online
    case offline
    case securityFailure

    var title: String {
        switch self {
        case .unknown:
            "sin comprobar"
        case .checking:
            "comprobando"
        case .online:
            "conectado"
        case .offline:
            "desconectado"
        case .securityFailure:
            "bloqueado"
        }
    }

    var symbol: String {
        switch self {
        case .unknown, .checking:
            "circle.dotted"
        case .online:
            "checkmark.circle.fill"
        case .offline:
            "circle"
        case .securityFailure:
            "exclamationmark.shield.fill"
        }
    }
}

enum SecurityMonitorState: String, Sendable {
    case unknown
    case checking
    case intact
    case compromised
    case unavailable

    var title: String {
        switch self {
        case .unknown:
            "sin comprobar"
        case .checking:
            "comprobando"
        case .intact:
            "íntegra"
        case .compromised:
            "comprometida"
        case .unavailable:
            "no disponible"
        }
    }

    var symbol: String {
        switch self {
        case .unknown, .checking:
            "circle.dotted"
        case .intact:
            "checkmark.shield.fill"
        case .compromised:
            "exclamationmark.shield.fill"
        case .unavailable:
            "shield.slash"
        }
    }
}

enum ProviderReadinessState: String, Sendable {
    case unknown
    case checking
    case configured
    case missing
    case unavailable

    var title: String {
        switch self {
        case .unknown: "sin comprobar"
        case .checking: "comprobando"
        case .configured: "configurado"
        case .missing: "sin credencial"
        case .unavailable: "no verificable"
        }
    }
}

enum VoiceTurnState: Equatable, Sendable {
    case idle
    case listening
    case submitting
    case processing
    case awaitingApproval
    case speaking
    case completed
    case failed

    var title: String {
        switch self {
        case .idle:
            "listo"
        case .listening:
            "escuchando"
        case .submitting:
            "enviando"
        case .processing:
            "procesando"
        case .awaitingApproval:
            "requiere aprobación"
        case .speaking:
            "respondiendo"
        case .completed:
            "completado"
        case .failed:
            "falló"
        }
    }

    var symbol: String {
        switch self {
        case .idle:
            "waveform"
        case .listening:
            "waveform.circle.fill"
        case .submitting:
            "arrow.up.circle.fill"
        case .processing:
            "brain.head.profile.fill"
        case .awaitingApproval:
            "exclamationmark.shield.fill"
        case .speaking:
            "speaker.wave.2.circle.fill"
        case .completed:
            "checkmark.circle.fill"
        case .failed:
            "exclamationmark.circle.fill"
        }
    }

    var isBusy: Bool {
        switch self {
        case .listening, .submitting, .processing:
            true
        case .idle, .awaitingApproval, .speaking, .completed, .failed:
            false
        }
    }
}

enum WakeWordEnrollmentState: Equatable, Sendable {
    case idle
    case loading
    case clearing
    case arming(WakeWordEnrollmentLabel)
    case recording(WakeWordEnrollmentLabel)
    case ready
    case failed(WakeWordEnrollmentError)

    var isBusy: Bool {
        switch self {
        case .loading, .clearing, .arming, .recording:
            true
        case .idle, .ready, .failed:
            false
        }
    }
}

enum SpeakerEnrollmentState: Equatable, Sendable {
    case idle
    case loading
    case updating
    case arming(SpeakerEnrollmentTarget)
    case recording(SpeakerEnrollmentTarget)
    case ready
    case failed(SpeakerEnrollmentError)

    var isBusy: Bool {
        switch self {
        case .loading, .updating, .arming, .recording:
            true
        case .idle, .ready, .failed:
            false
        }
    }
}

enum SpeakerModelTrainingState: Equatable, Sendable {
    case idle
    case training
    case ready
    case failed(SpeakerModelTrainingError)

    var isBusy: Bool {
        self == .training
    }
}

enum WakeWordListeningState: Equatable, Sendable {
    case unavailable
    case off
    case starting
    case recovering
    case listening
    case paused
    case failed
}

enum WakeWordPauseReason: Equatable, Sendable {
    case audio
    case system
    case runtime
    case thermal
    case lowPower
    case resuming

    var title: String {
        switch self {
        case .audio:
            "audio en uso"
        case .system:
            "sistema en reposo"
        case .runtime:
            "servicio no disponible"
        case .thermal:
            "presión térmica"
        case .lowPower:
            "bajo consumo"
        case .resuming:
            "reanudando"
        }
    }
}

struct PendingApproval: Equatable, Sendable {
    let jobID: UUID
    let confirmation: IPCPendingConfirmation
}

@MainActor
@Observable
final class MenuBarModel {
    var daemonState = DaemonConnectionState.unknown
    var securityState = SecurityMonitorState.unknown
    var providerState = ProviderReadinessState.unknown
    var runtimeProbeInProgress = false
    var voiceState = VoiceTurnState.idle
    var microphonePermission = MicrophonePermission.current
    var speechPermission = SpeechRecognitionPermission.current
    var screenCaptureAuthorized = ScreenCaptureService.isAuthorized
    var computerControlCapability = ComputerControlCapabilityState.helperUnavailable
    var hudActivity: [IPCSwarmAgentRole: Int] = [:]
    var voiceActivityLevel: Float = 0
    var lastSpeakerID: String?
    var voiceShortcutAvailable = false
    var wakeWordCapability = WakeWordCapabilityState.missing
    var speakerIdentityCapability = SpeakerIdentityCapabilityState.missing
    var wakeWordEnrollmentProgress = WakeWordEnrollmentProgress(
        jarvisCount: 0,
        backgroundCount: 0
    )
    var wakeWordEnrollmentState = WakeWordEnrollmentState.idle
    var speakerEnrollmentProgress = SpeakerEnrollmentProgress(
        backgroundCount: 0,
        profiles: []
    )
    var speakerEnrollmentState = SpeakerEnrollmentState.idle
    var speakerModelTrainingState = SpeakerModelTrainingState.idle
    var wakeWordListeningState = WakeWordListeningState.off
    var wakeWordPauseReason: WakeWordPauseReason?
    var wakeWordOptedIn = UserDefaults.standard.bool(forKey: wakeWordOptInDefaultsKey)
    var pendingApproval: PendingApproval?
    var activeComputerUseJobID: UUID?
    var approvalActionInProgress = false
    @ObservationIgnored private var ipcSecret: Data?
    @ObservationIgnored private var activeJobID: UUID?
    @ObservationIgnored private var conversationID = UserDefaults.standard
        .string(forKey: voiceConversationDefaultsKey)
        .flatMap(UUID.init(uuidString:))
    @ObservationIgnored private var conversationFollowUpTask: Task<Void, Never>?
    @ObservationIgnored private var monitoring = false
    @ObservationIgnored private var swarmMonitoring = false
    @ObservationIgnored private var computerBridgeMonitoring = false
    @ObservationIgnored private let wakeWordDetector = WakeWordDetector()
    @ObservationIgnored private var wakeWordResumeTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordRecoveryTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordStabilityTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordRecoveryGate = WakeWordRecoveryGate()
    @ObservationIgnored private var powerObservers: [any NSObjectProtocol] = []
    @ObservationIgnored private var privacyObservers: [any NSObjectProtocol] = []
    @ObservationIgnored private var privacyRefreshTask: Task<Void, Never>?
    @ObservationIgnored private var privacyRelaunchPending = false
    @ObservationIgnored private var privacyRelaunchInProgress = false
    @ObservationIgnored private var wakeWordThermalAvailable = WakeWordThermalPolicy
        .allowsListening(ProcessInfo.processInfo.thermalState)
    @ObservationIgnored private var wakeWordEnergyAvailable = WakeWordEnergyPolicy
        .allowsListening(lowPowerModeEnabled: ProcessInfo.processInfo.isLowPowerModeEnabled)
    @ObservationIgnored private let logger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "VoiceTurn"
    )
    @ObservationIgnored private let securityLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "SecurityMonitor"
    )
    @ObservationIgnored private let computerControlLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "ComputerControl"
    )
    @ObservationIgnored private let privacyLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "Privacy"
    )
    @ObservationIgnored private let swarmLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "SwarmEvents"
    )
    @ObservationIgnored private let wakeWordLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "WakeWord"
    )
    @ObservationIgnored private let speechOutput = SpeechOutput()

    var canStartVoiceTurn: Bool {
        daemonState == .online
            && securityState == .intact
            && providerState == .configured
            && microphonePermission == .authorized
            && speechPermission == .authorized
            && !voiceState.isBusy
            && !speakerModelTrainingState.isBusy
            && pendingApproval == nil
    }

    var canStartScreenTurn: Bool {
        canStartVoiceTurn && screenCaptureAuthorized
    }

    var canRecordWakeWordSample: Bool {
        microphonePermission == .authorized
            && !voiceState.isBusy
            && !wakeWordEnrollmentState.isBusy
            && !speakerEnrollmentState.isBusy
            && !speakerModelTrainingState.isBusy
    }

    var canModifySpeakerEnrollment: Bool {
        !voiceState.isBusy
            && !wakeWordEnrollmentState.isBusy
            && !speakerEnrollmentState.isBusy
            && !speakerModelTrainingState.isBusy
    }

    var canRecordSpeakerSample: Bool {
        microphonePermission == .authorized && canModifySpeakerEnrollment
    }

    private var wakeWordRuntimeAvailable: Bool {
        daemonState == .online
            && securityState == .intact
            && providerState == .configured
    }

    private var wakeWordMayResume: Bool {
        wakeWordOptedIn
            && wakeWordCapability == .ready
            && microphonePermission == .authorized
            && wakeWordRuntimeAvailable
            && wakeWordThermalAvailable
            && wakeWordEnergyAvailable
            && (wakeWordListeningState == .paused
                || wakeWordListeningState == .unavailable)
    }

    func startPowerMonitoring() {
        guard powerObservers.isEmpty else { return }
        let center = NSWorkspace.shared.notificationCenter
        powerObservers = [
            center.addObserver(
                forName: NSWorkspace.willSleepNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.suspendWakeWordForSystemSleep()
                }
            },
            center.addObserver(
                forName: NSWorkspace.didWakeNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.resumeWakeWordAfterSystemWake()
                }
            },
            NotificationCenter.default.addObserver(
                forName: ProcessInfo.thermalStateDidChangeNotification,
                object: ProcessInfo.processInfo,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.reconcileWakeWordThermalState()
                }
            },
            NotificationCenter.default.addObserver(
                forName: .NSProcessInfoPowerStateDidChange,
                object: ProcessInfo.processInfo,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.reconcileWakeWordEnergyState()
                }
            },
        ]
    }

    func startPrivacyChangeMonitoring() {
        guard privacyObservers.isEmpty else { return }
        let center = NSWorkspace.shared.notificationCenter
        privacyObservers = [
            center.addObserver(
                forName: NSWorkspace.didDeactivateApplicationNotification,
                object: nil,
                queue: .main
            ) { [weak self] notification in
                let bundleIdentifier = (
                    notification.userInfo?[NSWorkspace.applicationUserInfoKey]
                        as? NSRunningApplication
                )?.bundleIdentifier
                Task { @MainActor [weak self] in
                    guard bundleIdentifier == systemSettingsBundleIdentifier else { return }
                    self?.relaunchAfterPrivacyChangeIfNeeded()
                }
            },
        ]
    }

    func monitor() async {
        guard !monitoring else {
            return
        }
        monitoring = true
        defer { monitoring = false }
        while !Task.isCancelled {
            let previousMicrophonePermission = microphonePermission
            let previousRuntimeAvailable = wakeWordRuntimeAvailable
            refreshPermissions()
            reconcileWakeWordPermission(from: previousMicrophonePermission)
            await refreshDaemon()
            reconcileWakeWordRuntime(from: previousRuntimeAvailable)
            do {
                try await Task.sleep(for: .seconds(10))
            } catch {
                return
            }
        }
    }

    func runBackgroundMonitoring() async {
        async let runtime: Void = monitor()
        async let swarm: Void = monitorSwarmActivity()
        async let computer: Void = monitorComputerBridge()
        _ = await (runtime, swarm, computer)
    }

    private func monitorComputerBridge() async {
        guard !computerBridgeMonitoring else { return }
        computerBridgeMonitoring = true
        defer { computerBridgeMonitoring = false }
        var retryMilliseconds = 250
        while !Task.isCancelled {
            guard
                daemonState == .online,
                securityState == .intact,
                let ipcSecret
            else {
                do {
                    try await Task.sleep(for: .milliseconds(500))
                } catch {
                    return
                }
                continue
            }
            let available = await Task.detached(priority: .utility) { @Sendable [ipcSecret] in
                Self.relayNextComputerCommand(secret: ipcSecret)
            }.value
            guard !Task.isCancelled else { return }
            if available {
                retryMilliseconds = 250
                continue
            }
            do {
                try await Task.sleep(for: .milliseconds(retryMilliseconds))
            } catch {
                return
            }
            retryMilliseconds = min(retryMilliseconds * 2, 2_000)
        }
    }

    func refreshDaemon() async {
        guard !runtimeProbeInProgress else { return }
        runtimeProbeInProgress = true
        defer { runtimeProbeInProgress = false }
        let previousSecurityState = securityState
        if daemonState == .unknown {
            daemonState = .checking
        }
        if securityState == .unknown {
            securityState = .checking
        }
        if providerState == .unknown {
            providerState = .checking
        }
        let result = await Task.detached(priority: .utility) { [ipcSecret] in
            Self.probeDaemon(cachedSecret: ipcSecret)
        }.value
        daemonState = result.state
        if previousSecurityState != result.security {
            securityLogger.info(
                "audit_integrity_changed state=\(result.security.rawValue, privacy: .public)"
            )
        }
        securityState = result.security
        providerState = result.provider
        ipcSecret = result.secret
    }

    private func monitorSwarmActivity() async {
        guard !swarmMonitoring else {
            return
        }
        swarmMonitoring = true
        var version = 0
        var retryMilliseconds = 250
        defer {
            hudActivity = [:]
            swarmMonitoring = false
        }
        while !Task.isCancelled {
            guard
                daemonState == .online,
                securityState == .intact,
                let ipcSecret
            else {
                hudActivity = [:]
                version = 0
                do {
                    try await Task.sleep(for: .milliseconds(500))
                } catch {
                    return
                }
                continue
            }

            let update = await Task.detached(priority: .utility) { @Sendable [
                version,
                ipcSecret,
            ] in
                Self.fetchSwarmUpdate(afterVersion: version, secret: ipcSecret)
            }.value
            guard !Task.isCancelled else { return }
            guard daemonState == .online, securityState == .intact else {
                hudActivity = [:]
                continue
            }
            if let update {
                version = update.version
                hudActivity = Dictionary(
                    uniqueKeysWithValues: update.agents.map { ($0.role, $0.activeJobs) }
                )
                retryMilliseconds = 250
                if update.changed {
                    swarmLogger.debug(
                        "activity_changed version=\(update.version, privacy: .public) agents=\(update.agents.count, privacy: .public)"
                    )
                }
                continue
            }

            hudActivity = [:]
            do {
                try await Task.sleep(for: .milliseconds(retryMilliseconds))
            } catch {
                return
            }
            retryMilliseconds = min(retryMilliseconds * 2, 5_000)
        }
    }

    func requestMicrophone() async {
        guard MicrophonePermission.current == .notDetermined else {
            openPrivacySettings("Privacy_Microphone")
            beginPrivacyRefresh(for: .microphone)
            return
        }
        let previousMicrophonePermission = microphonePermission
        activateForPermissionPrompt()
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { _ in
                continuation.resume()
            }
        }
        refreshPermissions()
        reconcileWakeWordPermission(from: previousMicrophonePermission)
        beginPrivacyRefresh(for: .microphone)
    }

    func inspectWakeWordCapability() async {
        wakeWordCapability = await Task.detached(priority: .utility) {
            WakeWordCapability.inspect()
        }.value
    }

    func initializeWakeWordListening() async {
        let capabilities = await Task.detached(priority: .utility) {
            (
                WakeWordCapability.inspect(),
                SpeakerIdentityCapability.inspect(),
                ComputerControlService.inspect()
            )
        }.value
        wakeWordCapability = capabilities.0
        speakerIdentityCapability = capabilities.1
        computerControlCapability = capabilities.2
        computerControlLogger.info(
            "capability_initialized state=\(String(describing: capabilities.2), privacy: .public)"
        )
        logPrivacyCapabilities(event: "initialized")
        if speakerIdentityCapability == .ready {
            speakerModelTrainingState = .ready
        }
        guard wakeWordCapability == .ready else {
            wakeWordDetector.stop()
            wakeWordListeningState = .unavailable
            wakeWordPauseReason = nil
            return
        }
        if wakeWordOptedIn {
            await startWakeWordListening()
        } else {
            wakeWordListeningState = .off
            wakeWordPauseReason = nil
        }
    }

    func setWakeWordListeningEnabled(_ enabled: Bool) async {
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordOptedIn = enabled
        UserDefaults.standard.set(enabled, forKey: wakeWordOptInDefaultsKey)
        guard enabled else {
            wakeWordDetector.stop()
            wakeWordListeningState = .off
            wakeWordPauseReason = nil
            wakeWordLogger.info("wake_word_disabled")
            return
        }
        await inspectWakeWordCapability()
        guard wakeWordCapability == .ready else {
            wakeWordListeningState = .unavailable
            wakeWordPauseReason = nil
            return
        }
        await startWakeWordListening()
    }

    func refreshWakeWordEnrollment() async {
        guard !wakeWordEnrollmentState.isBusy else {
            return
        }
        wakeWordEnrollmentState = .loading
        let progress = await Task.detached(priority: .utility) {
            try? WakeWordEnrollmentRecorder().progress()
        }.value
        guard let progress else {
            wakeWordEnrollmentState = .failed(.unsafeStorage)
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = progress.isReady ? .ready : .idle
    }

    func recordWakeWordSample(_ label: WakeWordEnrollmentLabel) async {
        refreshPermissions()
        guard canRecordWakeWordSample else {
            return
        }
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        wakeWordEnrollmentState = .arming(label)
        do {
            try await Task.sleep(for: .seconds(1))
        } catch {
            wakeWordEnrollmentState = .idle
            return
        }
        wakeWordEnrollmentState = .recording(label)
        let outcome = await Task.detached(priority: .userInitiated) {
            do {
                return EnrollmentOutcome.success(
                    try WakeWordEnrollmentRecorder().record(label: label)
                )
            } catch let error as WakeWordEnrollmentError {
                return EnrollmentOutcome.failure(error)
            } catch {
                return EnrollmentOutcome.failure(.recordingFailed)
            }
        }.value
        guard case let .success(progress) = outcome else {
            if case let .failure(error) = outcome {
                wakeWordEnrollmentState = .failed(error)
            }
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = progress.isReady ? .ready : .idle
    }

    func clearWakeWordSamples() async {
        guard !wakeWordEnrollmentState.isBusy else {
            return
        }
        wakeWordEnrollmentState = .clearing
        let outcome = await Task.detached(priority: .utility) {
            do {
                return EnrollmentOutcome.success(
                    try WakeWordEnrollmentRecorder().clearSamples()
                )
            } catch let error as WakeWordEnrollmentError {
                return EnrollmentOutcome.failure(error)
            } catch {
                return EnrollmentOutcome.failure(.unsafeStorage)
            }
        }.value
        guard case let .success(progress) = outcome else {
            if case let .failure(error) = outcome {
                wakeWordEnrollmentState = .failed(error)
            }
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = .idle
    }

    func refreshSpeakerEnrollment() async {
        guard !speakerEnrollmentState.isBusy, !speakerModelTrainingState.isBusy else {
            return
        }
        speakerEnrollmentState = .loading
        let outcome = await Task.detached(priority: .utility) {
            do {
                return SpeakerEnrollmentOutcome.success(
                    try SpeakerEnrollmentRecorder().progress()
                )
            } catch let error as SpeakerEnrollmentError {
                return SpeakerEnrollmentOutcome.failure(error)
            } catch {
                return SpeakerEnrollmentOutcome.failure(.unsafeStorage)
            }
        }.value
        applySpeakerEnrollmentOutcome(outcome)
    }

    func trainSpeakerIdentityModel() async {
        guard
            speakerEnrollmentProgress.isReady,
            speakerIdentityCapability != .ready,
            !speakerModelTrainingState.isBusy,
            canModifySpeakerEnrollment
        else {
            return
        }
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        speakerModelTrainingState = .training
        let outcome = await Task.detached(priority: .utility) {
            do {
                try SpeakerModelTrainer().train()
                return SpeakerModelTrainingOutcome.success
            } catch let error as SpeakerModelTrainingError {
                return SpeakerModelTrainingOutcome.failure(error)
            } catch {
                return SpeakerModelTrainingOutcome.failure(.trainingFailed)
            }
        }.value
        switch outcome {
        case .success:
            speakerIdentityCapability = await Task.detached(priority: .utility) {
                SpeakerIdentityCapability.inspect()
            }.value
            speakerModelTrainingState = speakerIdentityCapability == .ready
                ? .ready
                : .failed(.invalidModel)
        case let .failure(error):
            speakerModelTrainingState = .failed(error)
        }
    }

    func addSpeakerProfile(_ identifier: String) async {
        await mutateSpeakerEnrollment(.add(identifier))
    }

    func removeSpeakerProfile(_ identifier: String) async {
        await mutateSpeakerEnrollment(.remove(identifier))
    }

    func clearSpeakerSamples() async {
        await mutateSpeakerEnrollment(.clearSamples)
    }

    private func mutateSpeakerEnrollment(_ mutation: SpeakerEnrollmentMutation) async {
        guard canModifySpeakerEnrollment else { return }
        speakerEnrollmentState = .updating
        let outcome = await Task.detached(priority: .utility) {
            do {
                let recorder = SpeakerEnrollmentRecorder()
                let progress = switch mutation {
                case let .add(identifier):
                    try recorder.addProfile(identifier)
                case let .remove(identifier):
                    try recorder.removeProfile(identifier)
                case .clearSamples:
                    try recorder.clearSamples()
                }
                return SpeakerEnrollmentOutcome.success(
                    progress
                )
            } catch let error as SpeakerEnrollmentError {
                return SpeakerEnrollmentOutcome.failure(error)
            } catch {
                return SpeakerEnrollmentOutcome.failure(.unsafeStorage)
            }
        }.value
        applySpeakerEnrollmentOutcome(outcome)
    }

    func recordSpeakerSample(_ target: SpeakerEnrollmentTarget) async {
        refreshPermissions()
        guard canRecordSpeakerSample else { return }
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        speakerEnrollmentState = .arming(target)
        do {
            try await Task.sleep(for: .seconds(1))
        } catch {
            speakerEnrollmentState = .idle
            return
        }
        speakerEnrollmentState = .recording(target)
        let outcome = await Task.detached(priority: .userInitiated) {
            do {
                return SpeakerEnrollmentOutcome.success(
                    try SpeakerEnrollmentRecorder().record(target: target)
                )
            } catch let error as SpeakerEnrollmentError {
                return SpeakerEnrollmentOutcome.failure(error)
            } catch {
                return SpeakerEnrollmentOutcome.failure(.recordingFailed)
            }
        }.value
        applySpeakerEnrollmentOutcome(outcome)
    }

    private func applySpeakerEnrollmentOutcome(_ outcome: SpeakerEnrollmentOutcome) {
        switch outcome {
        case let .success(progress):
            speakerEnrollmentProgress = progress
            speakerEnrollmentState = progress.isReady ? .ready : .idle
        case let .failure(error):
            speakerEnrollmentState = .failed(error)
        }
    }

    func requestSpeechRecognition() async {
        guard SpeechRecognitionPermission.current == .notDetermined else {
            openPrivacySettings("Privacy_SpeechRecognition")
            beginPrivacyRefresh(for: .speech)
            return
        }
        activateForPermissionPrompt()
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { _ in
                continuation.resume()
            }
        }
        refreshPermissions()
        beginPrivacyRefresh(for: .speech)
    }

    func requestScreenCapture() {
        guard !ScreenCaptureService.isAuthorized else {
            refreshPermissions()
            return
        }
        let defaults = UserDefaults.standard
        if defaults.bool(forKey: screenCaptureRequestDefaultsKey) {
            openPrivacySettings("Privacy_ScreenCapture")
            beginPrivacyRefresh(for: .screenCapture)
            return
        }
        defaults.set(true, forKey: screenCaptureRequestDefaultsKey)
        activateForPermissionPrompt()
        _ = ScreenCaptureService.requestAccess()
        screenCaptureAuthorized = ScreenCaptureService.isAuthorized
        beginPrivacyRefresh(for: .screenCapture)
    }

    func requestComputerControlAccess() async {
        await refreshComputerControlCapability()
        guard let request = computerControlCapability.nextPermissionRequest else { return }
        armRelaunchAfterPrivacyChange()
        activateForPermissionPrompt()
        ComputerControlService.requestPermission(request)
        beginPrivacyRefresh(for: .computerControl)
        do {
            try await Task.sleep(for: .seconds(1))
        } catch {
            return
        }
        await refreshComputerControlCapability()
    }

    func refreshComputerControlCapability() async {
        let capability = await Task.detached(priority: .utility) {
            ComputerControlService.inspect()
        }.value
        guard capability != computerControlCapability else { return }
        computerControlCapability = capability
        computerControlLogger.info(
            "capability_changed state=\(String(describing: capability), privacy: .public)"
        )
    }

    func refreshPrivacyCapabilities() async {
        refreshPermissions()
        await refreshComputerControlCapability()
        logPrivacyCapabilities(event: "refreshed")
    }

    func requestUndeterminedPermissions() async {
        if MicrophonePermission.current == .notDetermined {
            await requestMicrophone()
        }
        if SpeechRecognitionPermission.current == .notDetermined {
            await requestSpeechRecognition()
        }
    }

    func requestAllPrivacyPermissions() async {
        if MicrophonePermission.current != .authorized {
            await requestMicrophone()
            guard MicrophonePermission.current == .authorized else { return }
        }
        if SpeechRecognitionPermission.current != .authorized {
            await requestSpeechRecognition()
            guard SpeechRecognitionPermission.current == .authorized else { return }
        }
        if !ScreenCaptureService.isAuthorized {
            requestScreenCapture()
            guard ScreenCaptureService.isAuthorized else { return }
        }
        await requestComputerControlAccess()
    }

    func startVoiceTurn() async {
        guard !voiceState.isBusy else {
            return
        }
        conversationFollowUpTask?.cancel()
        conversationFollowUpTask = nil
        voiceActivityLevel = 0
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard
            canStartVoiceTurn,
            let secret = ipcSecret
        else {
            logger.error(
                "voice_turn_failed stage=preflight daemon=\(self.daemonState.title, privacy: .public) microphone=\(self.microphonePermission.rawValue, privacy: .public) speech=\(self.speechPermission.rawValue, privacy: .public)"
            )
            voiceState = .failed
            return
        }

        logger.info("voice_turn_started")
        let capture = await captureSpokenPrompt()
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture {
                logger.error(
                    "voice_turn_failed stage=capture reason=\(reason.rawValue, privacy: .public)"
                )
            }
            voiceState = .failed
            return
        }
        lastSpeakerID = transcript.speakerID
        await submitVoiceTranscript(transcript, secret: secret)
    }

    func startImageVoiceTurn(fileURL: URL) async {
        guard !voiceState.isBusy else {
            return
        }
        voiceActivityLevel = 0
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartVoiceTurn, let secret = ipcSecret else {
            logger.error("image_turn_failed stage=preflight")
            voiceState = .failed
            return
        }

        voiceState = .submitting
        let image = await Task.detached(priority: .userInitiated) {
            let accessed = fileURL.startAccessingSecurityScopedResource()
            defer {
                if accessed {
                    fileURL.stopAccessingSecurityScopedResource()
                }
            }
            return try? LocalImageEncoder.encodeFile(at: fileURL)
        }.value
        guard let image else {
            logger.error("image_turn_failed stage=encode")
            voiceState = .failed
            return
        }

        await startVisualVoiceTurn(image, secret: secret)
    }

    func startScreenVoiceTurn() async {
        guard !voiceState.isBusy else {
            return
        }
        voiceActivityLevel = 0
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartScreenTurn, let secret = ipcSecret else {
            logger.error("screen_turn_failed stage=preflight")
            voiceState = .failed
            return
        }

        voiceState = .submitting
        guard let image = try? await ScreenCaptureService.captureMainDisplay() else {
            logger.error("screen_turn_failed stage=capture")
            voiceState = .failed
            return
        }

        await startVisualVoiceTurn(image, secret: secret)
    }

    private func startVisualVoiceTurn(
        _ image: LocalImageAttachment,
        secret: Data
    ) async {
        logger.info("visual_voice_turn_started")

        let capture = await captureSpokenPrompt()
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture {
                logger.error(
                    "visual_turn_failed stage=voice reason=\(reason.rawValue, privacy: .public)"
                )
            }
            voiceState = .failed
            return
        }
        lastSpeakerID = transcript.speakerID

        voiceState = .submitting
        let activeConversationID = conversationID
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .image(image, prompt: transcript.text),
                conversationID: activeConversationID,
                secret: secret
            )
        }.value
        guard let submission else {
            logger.error("visual_turn_failed stage=submit")
            voiceState = .failed
            return
        }
        logger.info("visual_voice_turn_submitted")
        await trackSubmission(submission, secret: secret)
    }

    func approvePending() async {
        guard
            !approvalActionInProgress,
            let pendingApproval,
            let secret = ipcSecret
        else {
            return
        }
        approvalActionInProgress = true
        defer { approvalActionInProgress = false }
        logger.info(
            "tool_confirmation_approved tool=\(pendingApproval.confirmation.toolName, privacy: .public)"
        )
        let accepted = await Task.detached(priority: .userInitiated) {
            Self.approveJob(pendingApproval, secret: secret)
        }.value
        guard accepted else {
            logger.error("tool_confirmation_failed stage=approve")
            let outcome = await Self.waitForJob(pendingApproval.jobID, secret: secret)
            handleJobOutcome(outcome, jobID: pendingApproval.jobID)
            return
        }
        self.pendingApproval = nil
        activeJobID = pendingApproval.jobID
        voiceState = .processing
        let outcome = await Self.waitForJob(pendingApproval.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: pendingApproval.jobID)
    }

    func denyPending() async {
        guard
            !approvalActionInProgress,
            let pendingApproval,
            let secret = ipcSecret
        else {
            return
        }
        approvalActionInProgress = true
        defer { approvalActionInProgress = false }
        let denied = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(pendingApproval.jobID, secret: secret)
        }.value
        guard denied else {
            logger.error("tool_confirmation_failed stage=deny")
            return
        }
        logger.info(
            "tool_confirmation_denied tool=\(pendingApproval.confirmation.toolName, privacy: .public)"
        )
        self.pendingApproval = nil
        if activeJobID == pendingApproval.jobID {
            activeJobID = nil
        }
        if activeComputerUseJobID == pendingApproval.jobID {
            activeComputerUseJobID = nil
        }
        JarvisPointerController.shared.hide()
        voiceState = .idle
        speakWithWakeWordIsolation("Acción denegada.") {}
    }

    func cancelActiveComputerUse() async {
        guard
            let jobID = activeComputerUseJobID,
            activeJobID == jobID,
            let secret = ipcSecret
        else {
            return
        }
        let cancelled = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(jobID, secret: secret)
        }.value
        guard cancelled, activeJobID == jobID else {
            logger.error("computer_control_cancel_failed")
            return
        }
        activeJobID = nil
        activeComputerUseJobID = nil
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        voiceState = .idle
        logger.info("computer_control_cancelled")
    }

    private func handleJobOutcome(_ outcome: JobOutcome, jobID: UUID) {
        guard activeJobID == jobID else { return }
        switch outcome {
        case let .completed(result):
            activeJobID = nil
            activeComputerUseJobID = nil
            JarvisPointerController.shared.hide()
            speakCompletedResult(result)
        case let .awaitingConfirmation(confirmation):
            pendingApproval = PendingApproval(jobID: jobID, confirmation: confirmation)
            activeComputerUseJobID = confirmation.toolName == "computer_use" ? jobID : nil
            voiceState = .awaitingApproval
            logger.info(
                "tool_confirmation_requested tool=\(confirmation.toolName, privacy: .public)"
            )
            speakWithWakeWordIsolation("Se requiere tu aprobación en Jarvis.") {}
        case let .failed(errorCode):
            activeJobID = nil
            activeComputerUseJobID = nil
            pendingApproval = nil
            JarvisPointerController.shared.hide()
            logger.error(
                "voice_turn_failed stage=job reason=\(errorCode, privacy: .public)"
            )
            voiceState = .failed
        }
    }

    private func speakCompletedResult(_ result: String) {
        let spokenText = String(result.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ").prefix(2_000))
        guard !spokenText.isEmpty else {
            logger.error("voice_turn_failed stage=response")
            voiceState = .failed
            return
        }
        voiceState = .speaking
        speakWithWakeWordIsolation(spokenText) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
            self?.logger.info("voice_turn_completed")
            self?.scheduleConversationFollowUp()
        }
    }

    private func scheduleConversationFollowUp() {
        conversationFollowUpTask?.cancel()
        conversationFollowUpTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(250))
            } catch {
                return
            }
            guard let self, voiceState == .completed, canStartVoiceTurn else { return }
            conversationFollowUpTask = nil
            await startConversationFollowUp()
        }
    }

    private func startConversationFollowUp() async {
        guard let secret = ipcSecret else {
            voiceState = .failed
            return
        }
        logger.info("conversation_follow_up_started")
        let capture = await captureSpokenPrompt()
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture,
               reason == .noAudibleInput || reason == .noFinalTranscript {
                voiceState = .idle
                logger.info("conversation_follow_up_closed reason=silence")
            } else {
                voiceState = .failed
                logger.error("conversation_follow_up_failed stage=capture")
            }
            return
        }
        lastSpeakerID = transcript.speakerID
        await submitVoiceTranscript(transcript, secret: secret)
    }

    private func submitVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        secret: Data
    ) async {
        voiceState = .submitting
        let activeConversationID = conversationID
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .voice(transcript),
                conversationID: activeConversationID,
                secret: secret
            )
        }.value
        guard let submission else {
            logger.error("voice_turn_failed stage=submit")
            voiceState = .failed
            return
        }
        logger.info("voice_turn_submitted")
        await trackSubmission(submission, secret: secret)
    }

    private func captureSpokenPrompt() async -> CaptureOutcome {
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        voiceState = .listening
        NSSound.beep()
        let activityHandler: @Sendable (Float) -> Void = { [weak self] level in
            Task { @MainActor [weak self] in
                self?.voiceActivityLevel = level
            }
        }
        let capture = await Task.detached(priority: .userInitiated) {
            Self.captureTranscript(activityHandler: activityHandler)
        }.value
        voiceActivityLevel = 0
        return capture
    }

    private func startWakeWordListening() async {
        refreshPermissions()
        if wakeWordDetector.isRunning {
            wakeWordListeningState = .listening
            wakeWordPauseReason = nil
            return
        }
        guard
            wakeWordOptedIn,
            wakeWordCapability == .ready,
            microphonePermission == .authorized,
            !wakeWordEnrollmentState.isBusy
        else {
            wakeWordListeningState = microphonePermission == .authorized ? .failed : .unavailable
            wakeWordPauseReason = nil
            return
        }
        guard wakeWordThermalAvailable else {
            wakeWordListeningState = .paused
            wakeWordPauseReason = .thermal
            return
        }
        guard wakeWordEnergyAvailable else {
            wakeWordListeningState = .paused
            wakeWordPauseReason = .lowPower
            return
        }
        wakeWordListeningState = .starting
        wakeWordPauseReason = nil
        let detector = wakeWordDetector
        let detectionHandler: @Sendable () -> Void = { [weak self] in
            Task { @MainActor [weak self] in
                await self?.handleWakeWordDetection()
            }
        }
        let failureHandler: @Sendable () -> Void = { [weak self] in
            Task { @MainActor [weak self] in
                self?.wakeWordLogger.error("wake_word_failed stage=analysis")
                self?.handleWakeWordFailure()
            }
        }
        let started = await Task.detached(priority: .utility) {
            do {
                try detector.start(
                    detectionHandler: detectionHandler,
                    failureHandler: failureHandler
                )
                return true
            } catch {
                return false
            }
        }.value
        wakeWordListeningState = started ? .listening : .failed
        wakeWordPauseReason = nil
        if started {
            wakeWordLogger.info("wake_word_enabled")
            scheduleWakeWordStabilityReset()
        } else {
            wakeWordLogger.error("wake_word_failed stage=start")
            handleWakeWordFailure()
        }
    }

    private func handleWakeWordDetection() async {
        guard
            wakeWordListeningState == .listening,
            canStartVoiceTurn,
            voiceState != .speaking,
            voiceState != .awaitingApproval
        else {
            return
        }
        wakeWordLogger.info("wake_word_detected")
        let shouldResume = pauseWakeWordListening()
        await startVoiceTurn()
        scheduleWakeWordResume(if: shouldResume)
    }

    private func suspendWakeWordForSystemSleep() {
        guard wakeWordOptedIn else { return }
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordDetector.stop()
        wakeWordListeningState = wakeWordCapability == .ready ? .paused : .unavailable
        wakeWordPauseReason = wakeWordCapability == .ready ? .system : nil
        wakeWordLogger.info("wake_word_paused reason=system_sleep")
    }

    private func resumeWakeWordAfterSystemWake() {
        wakeWordThermalAvailable = WakeWordThermalPolicy.allowsListening(
            ProcessInfo.processInfo.thermalState
        )
        wakeWordEnergyAvailable = WakeWordEnergyPolicy.allowsListening(
            lowPowerModeEnabled: ProcessInfo.processInfo.isLowPowerModeEnabled
        )
        guard wakeWordMayResume else { return }
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .resuming
        wakeWordLogger.info("wake_word_resume_scheduled reason=system_wake")
        scheduleWakeWordResume(if: true)
    }

    private func reconcileWakeWordPermission(from previous: MicrophonePermission) {
        reconcileWakeWordAvailability(
            previousAvailable: previous == .authorized,
            currentAvailable: microphonePermission == .authorized,
            stoppedState: .unavailable,
            stoppedReason: nil,
            resumeReason: "microphone_authorized",
            pauseReason: "microphone_unavailable"
        )
    }

    private func reconcileWakeWordRuntime(from previousAvailable: Bool) {
        reconcileWakeWordAvailability(
            previousAvailable: previousAvailable,
            currentAvailable: wakeWordRuntimeAvailable,
            stoppedState: .paused,
            stoppedReason: .runtime,
            resumeReason: "runtime_available",
            pauseReason: "runtime_unavailable"
        )
    }

    private func reconcileWakeWordThermalState() {
        let previousAvailable = wakeWordThermalAvailable
        wakeWordThermalAvailable = WakeWordThermalPolicy.allowsListening(
            ProcessInfo.processInfo.thermalState
        )
        reconcileWakeWordAvailability(
            previousAvailable: previousAvailable,
            currentAvailable: wakeWordThermalAvailable,
            stoppedState: .paused,
            stoppedReason: .thermal,
            resumeReason: "thermal_available",
            pauseReason: "thermal_pressure"
        )
    }

    private func reconcileWakeWordEnergyState() {
        let previousAvailable = wakeWordEnergyAvailable
        wakeWordEnergyAvailable = WakeWordEnergyPolicy.allowsListening(
            lowPowerModeEnabled: ProcessInfo.processInfo.isLowPowerModeEnabled
        )
        reconcileWakeWordAvailability(
            previousAvailable: previousAvailable,
            currentAvailable: wakeWordEnergyAvailable,
            stoppedState: .paused,
            stoppedReason: .lowPower,
            resumeReason: "low_power_disabled",
            pauseReason: "low_power_enabled"
        )
    }

    private func reconcileWakeWordAvailability(
        previousAvailable: Bool,
        currentAvailable: Bool,
        stoppedState: WakeWordListeningState,
        stoppedReason: WakeWordPauseReason?,
        resumeReason: String,
        pauseReason: String
    ) {
        switch WakeWordAvailabilityPolicy.action(
            previousAvailable: previousAvailable,
            currentAvailable: currentAvailable,
            active: wakeWordOptedIn && wakeWordCapability == .ready,
            startEligible: wakeWordMayResume,
            detectorRunning: wakeWordDetector.isRunning
        ) {
        case .none:
            return
        case .start:
            wakeWordLogger.info(
                "wake_word_resume_requested reason=\(resumeReason, privacy: .public)"
            )
            wakeWordListeningState = .paused
            wakeWordPauseReason = .resuming
            scheduleWakeWordResume(if: true)
        case .stop:
            wakeWordResumeTask?.cancel()
            wakeWordResumeTask = nil
            cancelWakeWordRecovery(resetGate: true)
            wakeWordDetector.stop()
            wakeWordListeningState = stoppedState
            wakeWordPauseReason = stoppedReason
            wakeWordLogger.info("wake_word_paused reason=\(pauseReason, privacy: .public)")
        }
    }

    private func pauseWakeWordListening() -> Bool {
        guard wakeWordListeningState == .listening else {
            return false
        }
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = nil
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .audio
        return true
    }

    private func scheduleWakeWordResume(if shouldResume: Bool) {
        guard shouldResume, wakeWordOptedIn else {
            return
        }
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = Task { @MainActor [weak self] in
            guard let self else { return }
            var gate = WakeWordResumeGate()
            while !Task.isCancelled {
                let audioIsBusy = voiceState.isBusy
                    || speechOutput.isActive
                    || wakeWordEnrollmentState.isBusy
                if gate.observe(
                    audioIsBusy: audioIsBusy,
                    at: ProcessInfo.processInfo.systemUptime
                ) {
                    break
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
            guard !Task.isCancelled, wakeWordOptedIn else { return }
            await startWakeWordListening()
        }
    }

    private func handleWakeWordFailure() {
        wakeWordDetector.stop()
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = nil
        guard wakeWordOptedIn else {
            wakeWordListeningState = .off
            wakeWordPauseReason = nil
            return
        }
        guard wakeWordRecoveryTask == nil else {
            return
        }
        guard wakeWordRecoveryGate.consumeRetry() else {
            wakeWordListeningState = .failed
            wakeWordPauseReason = nil
            return
        }
        wakeWordListeningState = .recovering
        wakeWordPauseReason = nil
        wakeWordLogger.info("wake_word_recovery_scheduled delay_seconds=2")
        wakeWordRecoveryTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(2))
            } catch {
                self?.wakeWordRecoveryTask = nil
                return
            }
            guard let self, wakeWordOptedIn else { return }
            wakeWordRecoveryTask = nil
            await startWakeWordListening()
        }
    }

    private func scheduleWakeWordStabilityReset() {
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(30))
            } catch {
                return
            }
            guard
                let self,
                wakeWordListeningState == .listening,
                wakeWordDetector.isRunning
            else {
                return
            }
            wakeWordRecoveryGate.reset()
            wakeWordStabilityTask = nil
        }
    }

    private func cancelWakeWordRecovery(resetGate: Bool) {
        wakeWordRecoveryTask?.cancel()
        wakeWordRecoveryTask = nil
        wakeWordStabilityTask?.cancel()
        wakeWordStabilityTask = nil
        if resetGate {
            wakeWordRecoveryGate.reset()
        }
    }

    private func speakWithWakeWordIsolation(
        _ text: String,
        completion: @escaping () -> Void
    ) {
        let shouldResume = pauseWakeWordListening()
        speechOutput.speak(text, ipcSecret: ipcSecret) { [weak self] in
            completion()
            self?.scheduleWakeWordResume(if: shouldResume)
        }
    }

    private func trackSubmission(_ submission: SubmissionOutcome, secret: Data) async {
        conversationID = submission.conversationID
        UserDefaults.standard.set(
            submission.conversationID.uuidString.lowercased(),
            forKey: voiceConversationDefaultsKey
        )
        activeJobID = submission.jobID
        activeComputerUseJobID = nil
        voiceState = .processing
        let outcome = await Self.waitForJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
    }

    func openMicrophoneSettings() {
        openPrivacySettings("Privacy_Microphone")
    }

    func openSpeechSettings() {
        openPrivacySettings("Privacy_SpeechRecognition")
    }

    private func refreshPermissions() {
        microphonePermission = .current
        speechPermission = .current
        screenCaptureAuthorized = ScreenCaptureService.isAuthorized
    }

    private func activateForPermissionPrompt() {
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func logPrivacyCapabilities(event: String) {
        privacyLogger.info(
            "privacy_\(event, privacy: .public) microphone=\(self.microphonePermission.rawValue, privacy: .public) speech=\(self.speechPermission.rawValue, privacy: .public) screen=\(self.screenCaptureAuthorized, privacy: .public) control=\(String(describing: self.computerControlCapability), privacy: .public)"
        )
    }

    private func openPrivacySettings(_ pane: String) {
        guard let url = URL(
            string: "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?\(pane)"
        ) else {
            return
        }
        armRelaunchAfterPrivacyChange()
        NSWorkspace.shared.open(url)
    }

    private func beginPrivacyRefresh(for target: PrivacyRefreshTarget) {
        privacyRefreshTask?.cancel()
        privacyRefreshTask = Task { @MainActor [weak self] in
            guard let self else { return }
            for _ in 0 ..< 60 {
                guard !Task.isCancelled else { return }
                let previousMicrophonePermission = microphonePermission
                refreshPermissions()
                reconcileWakeWordPermission(from: previousMicrophonePermission)
                if case .computerControl = target {
                    await refreshComputerControlCapability()
                }
                let finished = switch target {
                case .microphone:
                    microphonePermission != .notDetermined
                case .speech:
                    speechPermission != .notDetermined
                case .screenCapture:
                    screenCaptureAuthorized
                case .computerControl:
                    computerControlCapability == .ready
                }
                if finished { return }
                do {
                    try await Task.sleep(for: .seconds(1))
                } catch {
                    return
                }
            }
        }
    }

    private func armRelaunchAfterPrivacyChange() {
        privacyRelaunchPending = true
    }

    private func relaunchAfterPrivacyChangeIfNeeded() {
        guard
            privacyRelaunchPending,
            !privacyRelaunchInProgress
        else { return }
        privacyRelaunchPending = false
        privacyRelaunchInProgress = true
        privacyRefreshTask?.cancel()

        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.createsNewApplicationInstance = true
        let currentProcessIdentifier = ProcessInfo.processInfo.processIdentifier
        NSWorkspace.shared.openApplication(
            at: Bundle.main.bundleURL,
            configuration: configuration
        ) { [weak self] application, _ in
            Task { @MainActor [weak self] in
                guard
                    let application,
                    application.processIdentifier != currentProcessIdentifier
                else {
                    self?.privacyRelaunchInProgress = false
                    return
                }
                do {
                    try await Task.sleep(for: .milliseconds(500))
                } catch {
                    self?.privacyRelaunchInProgress = false
                    return
                }
                guard !application.isTerminated else {
                    self?.privacyRelaunchInProgress = false
                    return
                }
                NSApplication.shared.terminate(nil)
            }
        }
    }

    nonisolated private static func probeDaemon(cachedSecret: Data?) -> ProbeResult {
        var resolvedSecret = cachedSecret
        do {
            if resolvedSecret == nil {
                resolvedSecret = try MacOSIPCSecretStore().get()
            }
            guard let resolvedSecret else {
                return ProbeResult(state: .offline, security: .unavailable, secret: nil)
            }
            let client = try LocalIPCClient(secret: resolvedSecret)
            let response = try client.health()
            let state: DaemonConnectionState =
                response.ok && response.payload["status"] as? String == "ok"
                ? .online : .offline
            guard state == .online else {
                return ProbeResult(
                    state: state,
                    security: .unavailable,
                    secret: resolvedSecret
                )
            }
            let providerResponse = try client.providerStatus()
            let provider = IPCProviderStatusEvent(response: providerResponse)
                .map { ProviderReadinessState(rawValue: $0.credential.rawValue) ?? .unavailable }
                ?? .unavailable
            let securityResponse = try client.securityStatus()
            guard let security = IPCSecurityStatusEvent(response: securityResponse) else {
                return ProbeResult(
                    state: state,
                    security: .compromised,
                    provider: provider,
                    secret: resolvedSecret
                )
            }
            return ProbeResult(
                state: state,
                security: security.integrity == .intact ? .intact : .compromised,
                provider: provider,
                secret: resolvedSecret
            )
        } catch let error as LocalIPCError {
            switch error {
            case .invalidCredential, .responseMismatch, .responseAuthenticationFailed:
                return ProbeResult(state: .securityFailure, security: .unavailable, secret: nil)
            case .unsafeSocket, .staleResponse, .malformedResponse:
                return ProbeResult(
                    state: .securityFailure,
                    security: .unavailable,
                    secret: resolvedSecret
                )
            default:
                return ProbeResult(
                    state: .offline,
                    security: .unavailable,
                    secret: resolvedSecret
                )
            }
        } catch {
            return ProbeResult(
                state: .offline,
                security: .unavailable,
                secret: resolvedSecret
            )
        }
    }

    nonisolated private static func captureTranscript(
        activityHandler: @escaping @Sendable (Float) -> Void
    ) -> CaptureOutcome {
        do {
            guard let transcript = try LocalSpeechTranscriber(
                writer: NDJSONWriter(handle: .nullDevice),
                activityHandler: activityHandler
            ).runForFinalTranscript(
                durationSeconds: 60,
                intervalMilliseconds: 50,
                localeIdentifier: "es-US"
            ) else {
                return .failed(.noFinalTranscript)
            }
            return .transcript(transcript)
        } catch let error as LocalSpeechTranscriberError {
            return .failed(CaptureFailure(error))
        } catch {
            return .failed(.unexpected)
        }
    }

    nonisolated private static func submitRequest(
        _ request: SubmissionRequest,
        conversationID: UUID?,
        secret: Data
    ) -> SubmissionOutcome? {
        guard let client = try? LocalIPCClient(secret: secret) else {
            return nil
        }

        func createConversation() -> UUID? {
            guard
                let response = try? client.createConversation(),
                let event = IPCConversationEvent(response: response)
            else {
                return nil
            }
            return event.conversationID
        }

        func send(conversationID: UUID) -> LocalIPCResponse? {
            switch request {
            case let .voice(transcript):
                try? client.submitVoiceTranscript(
                    transcript,
                    conversationID: conversationID
                )
            case let .image(image, prompt):
                try? client.submitImage(
                    text: prompt,
                    image: image,
                    conversationID: conversationID
                )
            }
        }

        guard var resolvedConversationID = conversationID ?? createConversation() else {
            return nil
        }
        guard var response = send(conversationID: resolvedConversationID) else {
            return nil
        }
        if !response.ok, response.errorCode == "conversation_not_found" {
            guard let replacement = createConversation() else { return nil }
            resolvedConversationID = replacement
            guard let retried = send(conversationID: replacement) else {
                return nil
            }
            response = retried
        }
        guard let submission = VoiceSubmissionEvent(response: response) else {
            return nil
        }
        return SubmissionOutcome(
            jobID: submission.jobID,
            conversationID: resolvedConversationID
        )
    }

    nonisolated private static func waitForJob(_ jobID: UUID, secret: Data) async -> JobOutcome {
        let startedAt = ProcessInfo.processInfo.systemUptime
        while ProcessInfo.processInfo.systemUptime - startedAt < 60 {
            guard let status = fetchJob(jobID, secret: secret), status.jobID == jobID else {
                return .failed("job_status_unavailable")
            }
            switch status.state {
            case .completed:
                guard let result = status.result else { return .failed("job_result_invalid") }
                return .completed(result)
            case .failed:
                return .failed(status.errorCode ?? "job_failed")
            case .cancelled:
                return .failed("job_cancelled")
            case .awaitingConfirmation:
                guard let confirmation = status.confirmation else {
                    return .failed("job_confirmation_invalid")
                }
                return .awaitingConfirmation(confirmation)
            case .queued, .running:
                let elapsed = ProcessInfo.processInfo.systemUptime - startedAt
                let delay = elapsed < 5 ? 100 : (elapsed < 15 ? 250 : 500)
                try? await Task.sleep(for: .milliseconds(delay))
            }
        }
        return .failed("job_timeout")
    }

    nonisolated private static func fetchJob(
        _ jobID: UUID,
        secret: Data
    ) -> IPCJobStatusEvent? {
        guard
            let response = try? LocalIPCClient(secret: secret).jobStatus(jobID)
        else {
            return nil
        }
        return IPCJobStatusEvent(response: response)
    }

    nonisolated private static func fetchSwarmUpdate(
        afterVersion: Int,
        secret: Data
    ) -> IPCSwarmActivityUpdate? {
        guard
            let response = try? LocalIPCClient(secret: secret).waitForSwarmActivity(
                afterVersion: afterVersion
            )
        else {
            return nil
        }
        return IPCSwarmActivityUpdate(response: response)
    }

    nonisolated private static func relayNextComputerCommand(secret: Data) -> Bool {
        guard let client = try? LocalIPCClient(secret: secret) else { return false }
        guard
            let pending = try? client.waitForComputerCommand(),
            pending.ok,
            let available = pending.payload["available"] as? Bool
        else {
            return false
        }
        guard available else { return true }
        guard
            let commandIDText = pending.payload["command_id"] as? String,
            let commandID = UUID(uuidString: commandIDText),
            let command = pending.payload["command"] as? [String: Any]
        else {
            return false
        }
        let pointerEvent = ComputerPointerEvent(command: command)
        let helperResponse = ComputerControlService.execute(command: command) ?? [
            "status": "error",
            "reason": "computer_helper_failed",
        ]
        if let pointerEvent {
            let succeeded = helperResponse["status"] as? String == "ok"
            DispatchQueue.main.async { @MainActor in
                JarvisPointerController.shared.present(pointerEvent, success: succeeded)
            }
        }
        guard
            let completed = try? client.completeComputerCommand(
                commandID,
                response: helperResponse
            ),
            completed.ok,
            completed.payload["accepted"] as? Bool == true
        else {
            return false
        }
        return true
    }

    nonisolated private static func approveJob(
        _ approval: PendingApproval,
        secret: Data
    ) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).approveJob(
                approval.jobID,
                callDigest: approval.confirmation.callDigest
            ),
            let status = IPCJobStatusEvent(response: response),
            status.jobID == approval.jobID,
            status.state == .running
        else {
            return false
        }
        return true
    }

    nonisolated private static func cancelJob(_ jobID: UUID, secret: Data) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).cancelJob(jobID),
            let status = IPCJobStatusEvent(response: response),
            status.jobID == jobID,
            status.state == .cancelled
        else {
            return false
        }
        return true
    }

    private struct ProbeResult: Sendable {
        let state: DaemonConnectionState
        let security: SecurityMonitorState
        let provider: ProviderReadinessState
        let secret: Data?

        init(
            state: DaemonConnectionState,
            security: SecurityMonitorState,
            provider: ProviderReadinessState = .unknown,
            secret: Data?
        ) {
            self.state = state
            self.security = security
            self.provider = provider
            self.secret = secret
        }
    }

    private enum EnrollmentOutcome: Sendable {
        case success(WakeWordEnrollmentProgress)
        case failure(WakeWordEnrollmentError)
    }

    private enum SpeakerEnrollmentOutcome: Sendable {
        case success(SpeakerEnrollmentProgress)
        case failure(SpeakerEnrollmentError)
    }

    private enum SpeakerModelTrainingOutcome: Sendable {
        case success
        case failure(SpeakerModelTrainingError)
    }

    private enum SpeakerEnrollmentMutation: Sendable {
        case add(String)
        case remove(String)
        case clearSamples
    }

    private struct SubmissionOutcome: Sendable {
        let jobID: UUID
        let conversationID: UUID
    }

    private enum SubmissionRequest: Sendable {
        case voice(SpeechTranscriptEvent)
        case image(LocalImageAttachment, prompt: String)
    }

    private enum JobOutcome: Sendable {
        case completed(String)
        case awaitingConfirmation(IPCPendingConfirmation)
        case failed(String)
    }

    private enum CaptureOutcome: Sendable {
        case transcript(SpeechTranscriptEvent)
        case failed(CaptureFailure)
    }

    private enum CaptureFailure: String, Equatable, Sendable {
        case invalidConfiguration
        case microphonePermission
        case speechPermission
        case unsupportedLocale
        case recognizerUnavailable
        case onDeviceRecognitionUnavailable
        case invalidInputFormat
        case noAudibleInput
        case recognitionFailed
        case noFinalTranscript
        case unexpected

        init(_ error: LocalSpeechTranscriberError) {
            switch error {
            case .invalidConfiguration:
                self = .invalidConfiguration
            case .microphonePermissionRequired:
                self = .microphonePermission
            case .speechPermissionRequired:
                self = .speechPermission
            case .unsupportedLocale:
                self = .unsupportedLocale
            case .recognizerUnavailable:
                self = .recognizerUnavailable
            case .onDeviceRecognitionUnavailable:
                self = .onDeviceRecognitionUnavailable
            case .invalidInputFormat:
                self = .invalidInputFormat
            case .noAudibleInput:
                self = .noAudibleInput
            case .recognitionFailed:
                self = .recognitionFailed
            }
        }
    }
}
