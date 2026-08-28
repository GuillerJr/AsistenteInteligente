import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech

private let voiceConversationDefaultsKey = "ai.aegis.voice.conversation-id"
private let voiceConversationLastUsedDefaultsKey = "ai.aegis.voice.conversation-last-used"
private let voiceConversationSpeakerDefaultsKey = "ai.aegis.voice.conversation-speaker-id"
private let voiceConversationModelDefaultsKey = "ai.aegis.voice.conversation-model-sha256"
private let speakerOwnerDefaultsKey = "ai.aegis.voice.owner-speaker-id"
private let speakerOwnerModelDefaultsKey = "ai.aegis.voice.owner-model-sha256"
private let wakeWordOptInDefaultsKey = "ai.aegis.voice.wake-word-enabled"
private let proactiveAlertsDefaultsKey = "ai.aegis.proactive-alerts-enabled"
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
    case session
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
        case .session:
            "sesión bloqueada"
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
    var localBrainAvailable = false
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
    var speakerIdentityIdentifiers: [String] = []
    var speakerIdentityModelFingerprint: String?
    var selectedSpeakerOwnerIdentifier: String? = {
        guard
            let identifier = UserDefaults.standard.string(forKey: speakerOwnerDefaultsKey),
            SpeakerIdentityCapability.isValidSpeakerLabel(identifier)
        else {
            return nil
        }
        return identifier
    }()
    var selectedSpeakerOwnerModelFingerprint: String? = {
        guard
            let value = UserDefaults.standard.string(forKey: speakerOwnerModelDefaultsKey),
            SpeakerIdentityCapability.isValidModelFingerprint(value)
        else {
            return nil
        }
        return value
    }()
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
    var proactiveAlertsEnabled = UserDefaults.standard.bool(
        forKey: proactiveAlertsDefaultsKey
    )
    var pendingApproval: PendingApproval?
    var activeComputerUseJobID: UUID?
    var approvalActionInProgress = false
    var lastEvaluation: IPCJobEvaluation?
    @ObservationIgnored private var ipcSecret: Data?
    @ObservationIgnored private var activeJobID: UUID?
    @ObservationIgnored private var conversationID = UserDefaults.standard
        .string(forKey: voiceConversationDefaultsKey)
        .flatMap(UUID.init(uuidString:))
    @ObservationIgnored private var conversationLastUsedAt: Date? = {
        let defaults = UserDefaults.standard
        guard
            defaults.object(forKey: voiceConversationLastUsedDefaultsKey) != nil
        else {
            return nil
        }
        let timestamp = defaults.double(forKey: voiceConversationLastUsedDefaultsKey)
        guard
            timestamp.isFinite
        else {
            return nil
        }
        return Date(timeIntervalSince1970: timestamp)
    }()
    @ObservationIgnored private var conversationSpeakerID: String? = {
        guard
            let value = UserDefaults.standard.string(
                forKey: voiceConversationSpeakerDefaultsKey
            ),
            SpeakerIdentityCapability.isValidSpeakerLabel(value)
        else {
            return nil
        }
        return value
    }()
    @ObservationIgnored private var conversationModelFingerprint: String? = {
        guard
            let value = UserDefaults.standard.string(forKey: voiceConversationModelDefaultsKey),
            SpeakerIdentityCapability.isValidModelFingerprint(value)
        else {
            return nil
        }
        return value
    }()
    @ObservationIgnored private var monitoring = false
    @ObservationIgnored private var swarmMonitoring = false
    @ObservationIgnored private var computerBridgeMonitoring = false
    @ObservationIgnored private let wakeWordDetector = WakeWordDetector()
    @ObservationIgnored private let ownerPresenceAuthenticator = OwnerPresenceAuthenticator()
    @ObservationIgnored private var ownerPresenceLease = OwnerPresenceLease()
    @ObservationIgnored private let userSessionExecutionGate = UserSessionExecutionGate()
    @ObservationIgnored private var userSessionAvailable = true
    @ObservationIgnored private var activeTranscriber: LocalSpeechTranscriber?
    @ObservationIgnored private let proactiveEventMonitor = ProactiveEventMonitor()
    @ObservationIgnored private var wakeWordResumeTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordRecoveryTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordStabilityTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordRecoveryGate = WakeWordRecoveryGate()
    @ObservationIgnored private var powerObservers: [any NSObjectProtocol] = []
    @ObservationIgnored private var privacyObservers: [any NSObjectProtocol] = []
    @ObservationIgnored private var privacyRefreshTask: Task<Void, Never>?
    @ObservationIgnored private var approvalExpiryTask: Task<Void, Never>?
    @ObservationIgnored private var privacyRelaunchPending = false
    @ObservationIgnored private var privacyRelaunchInProgress = false
    @ObservationIgnored private var wakeWordThermalAvailable = WakeWordThermalPolicy
        .allowsListening(ProcessInfo.processInfo.thermalState)
    @ObservationIgnored private var wakeWordThermalStateLabel = AcousticThermalEvent(
        state: ProcessInfo.processInfo.thermalState
    ).label
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
    @ObservationIgnored private let localVoiceTimer = LocalVoiceTimerScheduler()
    @ObservationIgnored private var speechStreamChunker = SpeechStreamChunker()
    @ObservationIgnored private var speechStreamOpen = false
    @ObservationIgnored private var voiceReplayBuffer = LocalVoiceReplayBuffer()

    var canStartVoiceTurn: Bool {
        userSessionAvailable
            && daemonState == .online
            && securityState == .intact
            && hybridBrainReady
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

    var effectiveSpeakerOwnerIdentifier: String? {
        return SpeakerOwnerPolicy.resolvedOwnerIdentifier(
            availableIdentifiers: speakerIdentityIdentifiers,
            selectedIdentifier: selectedSpeakerOwnerIdentifier,
            selectedModelFingerprint: selectedSpeakerOwnerModelFingerprint,
            activeModelFingerprint: speakerIdentityModelFingerprint
        )
    }

    var speakerOwnerSelectionNeedsReconfirmation: Bool {
        guard
            let selectedSpeakerOwnerIdentifier,
            speakerIdentityIdentifiers.contains(selectedSpeakerOwnerIdentifier)
        else {
            return false
        }
        return selectedSpeakerOwnerModelFingerprint != speakerIdentityModelFingerprint
    }

    private var wakeWordRuntimeAvailable: Bool {
        daemonState == .online
            && securityState == .intact
            && hybridBrainReady
    }

    var hybridBrainReady: Bool {
        providerState == .configured || localBrainAvailable
    }

    private var wakeWordMayResume: Bool {
        userSessionAvailable
            && wakeWordOptedIn
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
        wakeWordDetector.monitorThermalState { [weak self] event in
            Task { @MainActor [weak self] in
                self?.reconcileWakeWordThermalState(event)
            }
        }
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
            center.addObserver(
                forName: NSWorkspace.sessionDidResignActiveNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    await self?.suspendForUserSessionLock()
                }
            },
            center.addObserver(
                forName: NSWorkspace.sessionDidBecomeActiveNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.resumeAfterUserSessionUnlock()
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

    func initializeProactiveAlerts() async {
        guard proactiveAlertsEnabled else { return }
        proactiveAlertsEnabled = await proactiveEventMonitor.setEnabled(true)
        UserDefaults.standard.set(
            proactiveAlertsEnabled,
            forKey: proactiveAlertsDefaultsKey
        )
    }

    func setProactiveAlertsEnabled(_ enabled: Bool) async {
        proactiveAlertsEnabled = await proactiveEventMonitor.setEnabled(enabled)
        UserDefaults.standard.set(
            proactiveAlertsEnabled,
            forKey: proactiveAlertsDefaultsKey
        )
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
            if userSessionAvailable {
                let previousMicrophonePermission = microphonePermission
                refreshPermissions()
                reconcileWakeWordPermission(from: previousMicrophonePermission)
            }
            let previousRuntimeAvailable = wakeWordRuntimeAvailable
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
                userSessionAvailable,
                let executionPermit = userSessionExecutionGate.permit,
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
            let executionGate = userSessionExecutionGate
            let available = await Task.detached(priority: .utility) { @Sendable [ipcSecret] in
                Self.relayNextComputerCommand(
                    secret: ipcSecret,
                    executionGate: executionGate,
                    executionPermit: executionPermit
                )
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
        localBrainAvailable = result.localBrainAvailable
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
                SpeakerIdentityCapability.snapshot(),
                ComputerControlService.inspect()
            )
        }.value
        wakeWordCapability = capabilities.0
        applySpeakerIdentitySnapshot(capabilities.1)
        computerControlCapability = capabilities.2
        computerControlLogger.info(
            "capability_initialized state=\(String(describing: capabilities.2), privacy: .public)"
        )
        logPrivacyCapabilities(event: "initialized")
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

    func refreshSpeakerIdentityConfiguration() async {
        let snapshot = await Task.detached(priority: .utility) {
            SpeakerIdentityCapability.snapshot()
        }.value
        applySpeakerIdentitySnapshot(snapshot)
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
            let snapshot = await Task.detached(priority: .utility) {
                SpeakerIdentityCapability.snapshot()
            }.value
            applySpeakerIdentitySnapshot(snapshot)
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

    func setSpeakerOwnerIdentifier(_ identifier: String?) {
        guard canModifySpeakerEnrollment else { return }
        if let identifier {
            guard
                speakerIdentityIdentifiers.contains(identifier),
                let speakerIdentityModelFingerprint
            else {
                return
            }
            guard
                selectedSpeakerOwnerIdentifier != identifier
                    || selectedSpeakerOwnerModelFingerprint != speakerIdentityModelFingerprint
            else {
                return
            }
            selectedSpeakerOwnerIdentifier = identifier
            selectedSpeakerOwnerModelFingerprint = speakerIdentityModelFingerprint
            UserDefaults.standard.set(identifier, forKey: speakerOwnerDefaultsKey)
            UserDefaults.standard.set(
                speakerIdentityModelFingerprint,
                forKey: speakerOwnerModelDefaultsKey
            )
        } else {
            guard
                selectedSpeakerOwnerIdentifier != nil
                    || selectedSpeakerOwnerModelFingerprint != nil
            else {
                return
            }
            selectedSpeakerOwnerIdentifier = nil
            selectedSpeakerOwnerModelFingerprint = nil
            UserDefaults.standard.removeObject(forKey: speakerOwnerDefaultsKey)
            UserDefaults.standard.removeObject(forKey: speakerOwnerModelDefaultsKey)
        }
        clearVoiceConversationSession()
        logger.info(
            "speaker_owner_selection_changed configured=\(identifier != nil, privacy: .public)"
        )
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

    private func applySpeakerIdentitySnapshot(_ snapshot: SpeakerIdentityCapabilitySnapshot) {
        speakerIdentityCapability = snapshot.state
        speakerIdentityIdentifiers = snapshot.speakerIdentifiers
        speakerIdentityModelFingerprint = snapshot.modelFingerprint
        if
            snapshot.state == .ready,
            conversationSpeakerID != nil,
            conversationModelFingerprint != snapshot.modelFingerprint
        {
            clearVoiceConversationSession()
            logger.info("voice_conversation_reset source=speaker_model_changed")
        }
        if snapshot.state == .ready {
            speakerModelTrainingState = .ready
        } else if !speakerModelTrainingState.isBusy {
            speakerModelTrainingState = .idle
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
        guard !voiceState.isBusy, pendingApproval == nil else {
            return
        }
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
            announceVoiceFailure(preflightFailureMessage())
            return
        }

        logger.info("voice_turn_started")
        let capture = await captureSpokenPrompt()
        guard userSessionAvailable else {
            voiceState = .idle
            return
        }
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture {
                logger.error(
                    "voice_turn_failed stage=capture reason=\(reason.rawValue, privacy: .public)"
                )
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenCaptureFailure(for: reason.rawValue)
                )
            }
            return
        }
        lastSpeakerID = transcript.speakerID
        if await handleLocalVoiceReplay(transcript) {
            return
        }
        if await handleLocalVoiceConversation(transcript) {
            return
        }
        if handleLocalVoiceCapabilities(transcript.text) {
            return
        }
        if handleLocalVoiceSpeakerIdentity(transcript.text, speakerID: transcript.speakerID) {
            return
        }
        if handleLocalVoiceApplicationContext(transcript.text) {
            return
        }
        if handleLocalVoiceTimer(transcript.text) {
            return
        }
        await submitVoiceTranscript(transcript, secret: secret)
    }

    func startImageVoiceTurn(fileURL: URL) async {
        guard !voiceState.isBusy, pendingApproval == nil else {
            return
        }
        voiceActivityLevel = 0
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartVoiceTurn, let secret = ipcSecret else {
            logger.error("image_turn_failed stage=preflight")
            announceVoiceFailure(preflightFailureMessage())
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
            announceVoiceFailure("No pude leer esa imagen de forma segura.")
            return
        }

        await startVisualVoiceTurn(image, secret: secret)
    }

    func startScreenVoiceTurn() async {
        guard !voiceState.isBusy, pendingApproval == nil else {
            return
        }
        voiceActivityLevel = 0
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartScreenTurn, let secret = ipcSecret else {
            logger.error("screen_turn_failed stage=preflight")
            announceVoiceFailure(preflightFailureMessage(screenCaptureRequired: true))
            return
        }

        voiceState = .submitting
        guard let image = try? await ScreenCaptureService.captureMainDisplay() else {
            logger.error("screen_turn_failed stage=capture")
            announceVoiceFailure("No pude capturar la pantalla.")
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
        guard userSessionAvailable else {
            voiceState = .idle
            return
        }
        guard case let .transcript(transcript) = capture else {
            if case let .failed(reason) = capture {
                logger.error(
                    "visual_turn_failed stage=voice reason=\(reason.rawValue, privacy: .public)"
                )
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenCaptureFailure(for: reason.rawValue)
                )
            }
            return
        }
        lastSpeakerID = transcript.speakerID

        voiceState = .submitting
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit
        else {
            voiceState = .idle
            return
        }
        let conversationDecision = voiceConversationDecision(for: trustedTranscript)
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .image(image, transcript: trustedTranscript),
                conversationID: conversationDecision.conversationID,
                persistConversation: conversationDecision.persistAcceptedConversation,
                secret: secret
            )
        }.value
        guard userSessionExecutionGate.isCurrent(sessionPermit) else {
            if let submission {
                _ = await Task.detached(priority: .userInitiated) {
                    Self.cancelJob(submission.jobID, secret: secret)
                }.value
            }
            voiceState = .idle
            return
        }
        guard let submission else {
            logger.error("visual_turn_failed stage=submit")
            announceVoiceFailure("No pude comunicarme con el servicio local.")
            return
        }
        logger.info("visual_voice_turn_submitted")
        await trackSubmission(
            submission,
            conversationDecision: conversationDecision,
            secret: secret
        )
    }

    func approvePending() async {
        guard
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit,
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
        guard userSessionExecutionGate.isCurrent(sessionPermit) else {
            if accepted {
                _ = await Task.detached(priority: .userInitiated) {
                    Self.cancelJob(pendingApproval.jobID, secret: secret)
                }.value
            }
            return
        }
        guard accepted else {
            logger.error("tool_confirmation_failed stage=approve")
            let outcome = await awaitJob(pendingApproval.jobID, secret: secret)
            handleJobOutcome(outcome, jobID: pendingApproval.jobID)
            return
        }
        cancelApprovalExpiry()
        self.pendingApproval = nil
        activeJobID = pendingApproval.jobID
        voiceState = .processing
        let outcome = await awaitJob(pendingApproval.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: pendingApproval.jobID)
    }

    func denyPending() async {
        guard
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit,
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
        guard userSessionExecutionGate.isCurrent(sessionPermit) else { return }
        guard denied else {
            logger.error("tool_confirmation_failed stage=deny")
            return
        }
        logger.info(
            "tool_confirmation_denied tool=\(pendingApproval.confirmation.toolName, privacy: .public)"
        )
        cancelApprovalExpiry()
        self.pendingApproval = nil
        if activeJobID == pendingApproval.jobID {
            activeJobID = nil
        }
        if activeComputerUseJobID == pendingApproval.jobID {
            activeComputerUseJobID = nil
        }
        JarvisPointerController.shared.hide()
        voiceState = .speaking
        speakWithWakeWordIsolation("Entendido. No ejecuté la acción.") { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
        }
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
        cancelApprovalExpiry()
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
            cancelApprovalExpiry()
            JarvisPointerController.shared.hide()
            speakCompletedResult(result)
        case let .awaitingConfirmation(confirmation):
            let approval = PendingApproval(jobID: jobID, confirmation: confirmation)
            pendingApproval = approval
            activeComputerUseJobID = confirmation.toolName == "computer_use" ? jobID : nil
            voiceState = .awaitingApproval
            scheduleApprovalExpiry(for: approval)
            logger.info(
                "tool_confirmation_requested tool=\(confirmation.toolName, privacy: .public)"
            )
            speakWithWakeWordIsolation(
                "Necesito tu aprobación. Revisa la ventana que abrí."
            ) {}
        case let .failed(errorCode):
            activeJobID = nil
            activeComputerUseJobID = nil
            cancelApprovalExpiry()
            pendingApproval = nil
            if speechStreamOpen {
                speechOutput.stop()
                speechStreamChunker.reset()
                speechStreamOpen = false
            }
            JarvisPointerController.shared.hide()
            logger.error(
                "voice_turn_failed stage=job reason=\(errorCode, privacy: .public)"
            )
            announceVoiceFailure(LocalVoiceTurnFeedback.spokenJobFailure(for: errorCode))
        }
    }

    private func scheduleApprovalExpiry(for approval: PendingApproval) {
        cancelApprovalExpiry()
        let delay = LocalVoiceTurnFeedback.approvalExpiryDelay(
            expiresAt: approval.confirmation.expiresAt
        )
        approvalExpiryTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(Int64(delay * 1_000)))
            } catch {
                return
            }
            guard
                let self,
                !Task.isCancelled,
                userSessionAvailable,
                !approvalActionInProgress,
                pendingApproval == approval,
                activeJobID == approval.jobID,
                let secret = ipcSecret
            else {
                return
            }
            approvalActionInProgress = true
            defer { approvalActionInProgress = false }
            let outcome = await awaitJob(approval.jobID, secret: secret)
            guard pendingApproval == approval else { return }
            approvalExpiryTask = nil
            handleJobOutcome(outcome, jobID: approval.jobID)
        }
    }

    private func cancelApprovalExpiry() {
        approvalExpiryTask?.cancel()
        approvalExpiryTask = nil
    }

    private func preflightFailureMessage(
        screenCaptureRequired: Bool = false
    ) -> String {
        LocalVoiceTurnFeedback.spokenPreflightFailure(
            microphoneAuthorized: microphonePermission == .authorized,
            speechRecognitionAuthorized: speechPermission == .authorized,
            runtimeAvailable: daemonState == .online
                && securityState == .intact
                && hybridBrainReady
                && ipcSecret != nil,
            screenCaptureAuthorized: screenCaptureRequired ? screenCaptureAuthorized : nil
        )
    }

    private func announceVoiceFailure(_ response: String?) {
        if speechStreamOpen {
            speechOutput.stop()
            speechStreamChunker.reset()
            speechStreamOpen = false
        }
        guard let response else {
            voiceState = .idle
            return
        }
        voiceState = .speaking
        speakWithWakeWordIsolation(response, localOnly: true) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .failed
        }
    }

    private func speakCompletedResult(_ result: String) {
        let normalized = result.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        if speechStreamOpen {
            let chunks = speechStreamChunker.finish(normalized)
            guard !speechStreamChunker.isInvalid else {
                logger.error("voice_turn_failed stage=stream_integrity")
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenJobFailure(for: "job_stream_invalid")
                )
                return
            }
            voiceReplayBuffer.store(
                normalized,
                at: ProcessInfo.processInfo.systemUptime
            )
            for chunk in chunks {
                speechOutput.enqueue(chunk)
            }
            speechOutput.finishStream()
            return
        }
        let spokenText = String(normalized.prefix(2_000))
        guard !spokenText.isEmpty else {
            logger.error("voice_turn_failed stage=response")
            announceVoiceFailure(
                LocalVoiceTurnFeedback.spokenJobFailure(for: "empty_agent_response")
            )
            return
        }
        voiceReplayBuffer.store(
            spokenText,
            at: ProcessInfo.processInfo.systemUptime
        )
        voiceState = .speaking
        speakWithWakeWordIsolation(spokenText) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
            self?.logger.info("voice_turn_completed")
        }
    }

    private func submitVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        secret: Data
    ) async {
        voiceState = .submitting
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit
        else {
            voiceState = .idle
            return
        }
        let conversationDecision = voiceConversationDecision(for: trustedTranscript)
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .voice(trustedTranscript),
                conversationID: conversationDecision.conversationID,
                persistConversation: conversationDecision.persistAcceptedConversation,
                secret: secret
            )
        }.value
        guard userSessionExecutionGate.isCurrent(sessionPermit) else {
            if let submission {
                _ = await Task.detached(priority: .userInitiated) {
                    Self.cancelJob(submission.jobID, secret: secret)
                }.value
            }
            voiceState = .idle
            return
        }
        guard let submission else {
            logger.error("voice_turn_failed stage=submit")
            announceVoiceFailure("No pude comunicarme con el servicio local.")
            return
        }
        logger.info("voice_turn_submitted")
        await trackSubmission(
            submission,
            conversationDecision: conversationDecision,
            secret: secret
        )
    }

    private func transcriptWithOwnerPresence(
        _ transcript: SpeechTranscriptEvent
    ) async -> SpeechTranscriptEvent? {
        var presenceVerified = false
        if transcript.ownerSpeakerProfile, userSessionAvailable {
            let uptime = ProcessInfo.processInfo.systemUptime
            if ownerPresenceLease.isAuthorized(at: uptime) {
                presenceVerified = true
            } else if await ownerPresenceAuthenticator.verify(), userSessionAvailable {
                presenceVerified = ownerPresenceLease.authorize(
                    at: ProcessInfo.processInfo.systemUptime
                )
            }
            logger.info(
                "owner_presence_checked verified=\(presenceVerified, privacy: .public)"
            )
        }
        return SpeechTranscriptEvent(
            captureID: transcript.captureID,
            sequence: transcript.sequence,
            text: transcript.text,
            localeIdentifier: transcript.localeIdentifier,
            durationMilliseconds: transcript.durationMilliseconds,
            isFinal: transcript.isFinal,
            confidence: transcript.confidence,
            speakerID: transcript.speakerID,
            speakerConfidence: transcript.speakerConfidence,
            soleSpeakerProfile: presenceVerified && transcript.soleSpeakerProfile,
            ownerSpeakerProfile: presenceVerified && transcript.ownerSpeakerProfile,
            ownerPresenceVerified: presenceVerified
        )
    }

    private func handleLocalVoiceReplay(_ transcript: SpeechTranscriptEvent) async -> Bool {
        guard LocalVoiceReplayCommand.parse(transcript.text) != nil else { return false }
        guard
            let response = voiceReplayBuffer.response(
                at: ProcessInfo.processInfo.systemUptime
            )
        else {
            speakLocalVoiceReplay(
                LocalVoiceReplayCommand.unavailableSpokenResponse,
                event: "unavailable"
            )
            return true
        }
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            trustedTranscript.ownerPresenceVerified,
            trustedTranscript.ownerSpeakerProfile,
            userSessionAvailable
        else {
            logger.info("voice_replay_rejected reason=owner_unverified")
            speakLocalVoiceReplay(
                LocalVoiceReplayCommand.unverifiedSpokenResponse,
                event: "unverified"
            )
            return true
        }
        guard
            voiceReplayBuffer.response(at: ProcessInfo.processInfo.systemUptime)
                == response
        else {
            speakLocalVoiceReplay(
                LocalVoiceReplayCommand.unavailableSpokenResponse,
                event: "expired"
            )
            return true
        }
        logger.info("voice_replay_started")
        speakLocalVoiceReplay(response, event: "replayed")
        return true
    }

    private func speakLocalVoiceReplay(_ text: String, event: String) {
        voiceState = .speaking
        speakWithWakeWordIsolation(text, localOnly: true) { [weak self] in
            guard let self, voiceState == .speaking else { return }
            voiceState = .completed
            logger.info("voice_replay_completed event=\(event, privacy: .public)")
        }
    }

    private func handleLocalVoiceCapabilities(_ text: String) -> Bool {
        guard LocalVoiceCapabilitiesCommand.parse(text) != nil else { return false }
        let visualControlReady = computerControlCapability == .ready
        logger.info(
            "local_capabilities visual_control_ready=\(visualControlReady, privacy: .public)"
        )
        speakLocalVoiceUtility(
            LocalVoiceCapabilitiesCommand.spokenResponse(
                visualControlReady: visualControlReady
            ),
            utility: "capabilities",
            event: visualControlReady ? "full" : "visual_control_unavailable"
        )
        return true
    }

    private func handleLocalVoiceConversation(_ transcript: SpeechTranscriptEvent) async -> Bool {
        guard LocalVoiceConversationCommand.parse(transcript.text) != nil else { return false }
        guard
            let trustedTranscript = await transcriptWithOwnerPresence(transcript),
            userSessionAvailable
        else {
            return true
        }
        let boundSpeakerMatches = trustedTranscript.ownerPresenceVerified
            && trustedTranscript.ownerSpeakerProfile
            && trustedTranscript.speakerID == conversationSpeakerID
            && speakerIdentityModelFingerprint == conversationModelFingerprint
        let resetRequiresVerifiedSpeaker = conversationSpeakerID != nil
            || speakerIdentityCapability == .ready
        if resetRequiresVerifiedSpeaker,
           !(
               boundSpeakerMatches
                   || (
                       conversationSpeakerID == nil
                           && trustedTranscript.ownerSpeakerProfile
                           && trustedTranscript.ownerPresenceVerified
                   )
           )
        {
            logger.info("voice_conversation_reset_rejected reason=speaker_unverified")
            speakLocalVoiceUtility(
                LocalVoiceConversationCommand.unverifiedSpokenResponse,
                utility: "conversation",
                event: "unverified"
            )
            return true
        }
        clearVoiceConversationSession()
        logger.info("voice_conversation_reset source=explicit_command")
        speakLocalVoiceUtility(
            LocalVoiceConversationCommand.spokenResponse,
            utility: "conversation",
            event: "reset"
        )
        return true
    }

    private func handleLocalVoiceSpeakerIdentity(_ text: String, speakerID: String?) -> Bool {
        guard LocalVoiceSpeakerIdentityCommand.parse(text) != nil else { return false }
        let spokenIdentifier = LocalVoiceSpeakerIdentityCommand.spokenIdentifier(speakerID)
        logger.info(
            "local_speaker_identity recognized=\(spokenIdentifier != nil, privacy: .public) capability=\(self.speakerIdentityCapability.rawValue, privacy: .public)"
        )
        if let spokenIdentifier {
            speakLocalVoiceUtility(
                "Te reconozco como \(spokenIdentifier).",
                utility: "speaker_identity",
                event: "recognized"
            )
        } else if speakerIdentityCapability == .ready {
            speakLocalVoiceUtility(
                "No pude reconocerte con suficiente confianza en este turno.",
                utility: "speaker_identity",
                event: "not_recognized"
            )
        } else {
            speakLocalVoiceUtility(
                "La identidad de voz aún no está configurada.",
                utility: "speaker_identity",
                event: "unavailable"
            )
        }
        return true
    }

    private func handleLocalVoiceApplicationContext(_ text: String) -> Bool {
        guard LocalVoiceApplicationContextCommand.parse(text) != nil else { return false }
        let applicationName = LocalVoiceApplicationContextCommand.sanitizedApplicationName(
            NSWorkspace.shared.frontmostApplication?.localizedName
        )
        logger.info("local_application_context resolved=\(applicationName != nil, privacy: .public)")
        speakLocalVoiceUtility(
            applicationName.map { "La aplicación activa es \($0)." }
                ?? "No pude identificar la aplicación activa.",
            utility: "application_context",
            event: applicationName == nil ? "unavailable" : "resolved"
        )
        return true
    }

    private func handleLocalVoiceTimer(_ text: String) -> Bool {
        guard let command = LocalVoiceTimerCommand.parse(text) else { return false }
        switch command {
        case let .start(durationSeconds):
            let started = localVoiceTimer.start(durationSeconds: durationSeconds) { [weak self] in
                self?.announceLocalVoiceTimerCompletion()
            }
            guard started else {
                speakLocalVoiceUtility(
                    "Ya hay un temporizador activo.",
                    utility: "timer",
                    event: "rejected"
                )
                return true
            }
            logger.info("voice_timer_started duration_seconds=\(durationSeconds, privacy: .public)")
            speakLocalVoiceUtility(
                "Temporizador iniciado por \(Self.spokenTimerDuration(durationSeconds)).",
                utility: "timer",
                event: "started"
            )
        case .cancel:
            let cancelled = localVoiceTimer.cancel()
            logger.info("voice_timer_cancelled active=\(cancelled, privacy: .public)")
            speakLocalVoiceUtility(
                cancelled ? "Temporizador cancelado." : "No hay un temporizador activo.",
                utility: "timer",
                event: cancelled ? "cancelled" : "missing"
            )
        case .pause:
            guard let remainingSeconds = localVoiceTimer.pause() else {
                speakLocalVoiceUtility(
                    localVoiceTimer.isPaused
                        ? "El temporizador ya está pausado."
                        : "No hay un temporizador en marcha.",
                    utility: "timer",
                    event: localVoiceTimer.isPaused ? "already_paused" : "missing"
                )
                return true
            }
            logger.info("voice_timer_paused remaining_seconds=\(remainingSeconds, privacy: .public)")
            speakLocalVoiceUtility(
                "Temporizador pausado. Quedaban \(Self.spokenTimerDuration(remainingSeconds)).",
                utility: "timer",
                event: "paused"
            )
        case .resume:
            guard let remainingSeconds = localVoiceTimer.resume() else {
                speakLocalVoiceUtility(
                    localVoiceTimer.isActive
                        ? "El temporizador ya está en marcha."
                        : "No hay un temporizador pausado.",
                    utility: "timer",
                    event: localVoiceTimer.isActive ? "already_running" : "missing"
                )
                return true
            }
            logger.info("voice_timer_resumed remaining_seconds=\(remainingSeconds, privacy: .public)")
            speakLocalVoiceUtility(
                "Temporizador reanudado. Quedan \(Self.spokenTimerDuration(remainingSeconds)).",
                utility: "timer",
                event: "resumed"
            )
        case .status:
            guard let remainingSeconds = localVoiceTimer.remainingSeconds else {
                speakLocalVoiceUtility(
                    "No hay un temporizador activo.",
                    utility: "timer",
                    event: "missing"
                )
                return true
            }
            logger.info("voice_timer_status remaining_seconds=\(remainingSeconds, privacy: .public)")
            let state = localVoiceTimer.isPaused ? "El temporizador está pausado. Quedan" : "Quedan"
            speakLocalVoiceUtility(
                "\(state) \(Self.spokenTimerDuration(remainingSeconds)).",
                utility: "timer",
                event: "status"
            )
        }
        return true
    }

    private func announceLocalVoiceTimerCompletion() {
        NSSound.beep()
        guard voiceState == .idle || voiceState == .completed || voiceState == .failed else {
            logger.info("voice_timer_completed announcement=beep_only")
            return
        }
        speakLocalVoiceUtility(
            "El temporizador terminó.",
            utility: "timer",
            event: "completed"
        )
    }

    private func speakLocalVoiceUtility(_ text: String, utility: String, event: String) {
        voiceReplayBuffer.store(text, at: ProcessInfo.processInfo.systemUptime)
        voiceState = .speaking
        speakWithWakeWordIsolation(text) { [weak self] in
            guard let self, voiceState == .speaking else { return }
            voiceState = .completed
            logger.info(
                "local_voice_utility_announced utility=\(utility, privacy: .public) event=\(event, privacy: .public)"
            )
        }
    }

    private static func spokenTimerDuration(_ seconds: Int) -> String {
        let hours = seconds / 3_600
        let minutes = (seconds % 3_600) / 60
        let remainingSeconds = seconds % 60
        var parts: [String] = []
        if hours > 0 {
            parts.append(hours == 1 ? "1 hora" : "\(hours) horas")
        }
        if minutes > 0 {
            parts.append(minutes == 1 ? "1 minuto" : "\(minutes) minutos")
        }
        if remainingSeconds > 0 || parts.isEmpty {
            parts.append(
                remainingSeconds == 1 ? "1 segundo" : "\(remainingSeconds) segundos"
            )
        }
        guard parts.count > 1 else { return parts[0] }
        return parts.dropLast().joined(separator: ", ") + " y " + parts.last!
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
        let selectedOwnerIdentifier = effectiveSpeakerOwnerIdentifier
        let speakerModelFingerprint = speakerIdentityModelFingerprint
        let transcriber = LocalSpeechTranscriber(
            writer: NDJSONWriter(handle: .nullDevice),
            activityHandler: activityHandler,
            selectedOwnerIdentifier: selectedOwnerIdentifier,
            expectedSpeakerModelFingerprint: speakerModelFingerprint
        )
        activeTranscriber = transcriber
        defer {
            if activeTranscriber === transcriber {
                activeTranscriber = nil
            }
        }
        let capture = await Task.detached(priority: .userInitiated) {
            Self.captureTranscript(with: transcriber)
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
            userSessionAvailable,
            wakeWordOptedIn,
            wakeWordCapability == .ready,
            microphonePermission == .authorized,
            !wakeWordEnrollmentState.isBusy
        else {
            if !userSessionAvailable, wakeWordCapability == .ready {
                wakeWordListeningState = .paused
                wakeWordPauseReason = .session
            } else {
                wakeWordListeningState = microphonePermission == .authorized
                    ? .failed
                    : .unavailable
                wakeWordPauseReason = nil
            }
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
        let acousticAuditSecret = ipcSecret
        let activityHandler: @Sendable (AcousticActivityEvent) -> Void = { event in
            guard let secret = acousticAuditSecret else { return }
            Task.detached(priority: .utility) {
                _ = Self.recordAcousticAuditEvent(
                    "noise_floor_transition",
                    data: [
                        "rms_microunits": Int((event.rms * 1_000_000).rounded()),
                        "noise_floor_microunits": Int(
                            (event.noiseFloor * 1_000_000).rounded()
                        ),
                        "threshold_microunits": Int(
                            (event.activeThreshold * 1_000_000).rounded()
                        ),
                        "voice_active": event.voiceActive,
                    ],
                    secret: secret
                )
            }
        }
        let started = await Task.detached(priority: .utility) {
            do {
                try detector.start(
                    detectionHandler: detectionHandler,
                    failureHandler: failureHandler,
                    activityHandler: activityHandler
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
        guard wakeWordListeningState == .listening else { return }
        wakeWordLogger.info("wake_word_detected")
        if voiceState == .speaking || voiceState == .processing || voiceState == .submitting {
            await interruptCurrentTurnAndListen()
            return
        }
        guard canStartVoiceTurn, voiceState != .awaitingApproval else { return }
        let shouldResume = pauseWakeWordListening()
        await startVoiceTurn()
        scheduleWakeWordResume(if: shouldResume)
    }

    private func interruptCurrentTurnAndListen() async {
        wakeWordLogger.info("voice_turn_interrupted source=wake_word")
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .audio
        speechOutput.stop()
        speechStreamChunker.reset()
        speechStreamOpen = false
        guard let secret = ipcSecret else {
            wakeWordLogger.error("voice_turn_interruption_failed stage=ipc_auth")
            voiceState = .failed
            return
        }
        var jobCancellationSucceeded = true
        let interruptedJobID = activeJobID
        if let jobID = interruptedJobID {
            jobCancellationSucceeded = await Task.detached(priority: .userInitiated) {
                Self.cancelJob(jobID, secret: secret)
            }.value
        }
        let auditSucceeded = await Task.detached(priority: .utility) {
            Self.recordAcousticAuditEvent(
                "voice_interruption",
                data: [
                    "job_present": interruptedJobID != nil,
                    "job_cancelled": jobCancellationSucceeded,
                    "source": "wake_word",
                ],
                secret: secret
            )
        }.value
        guard jobCancellationSucceeded, auditSucceeded else {
            let stage = jobCancellationSucceeded ? "audit" : "job_cancel"
            wakeWordLogger.error("voice_turn_interruption_failed stage=\(stage, privacy: .public)")
            voiceState = .failed
            return
        }
        activeJobID = nil
        activeComputerUseJobID = nil
        cancelApprovalExpiry()
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        voiceState = .idle
        await startVoiceTurn()
    }

    private func suspendWakeWordForSystemSleep() {
        voiceReplayBuffer.clear()
        guard wakeWordOptedIn else { return }
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordDetector.stop()
        wakeWordListeningState = wakeWordCapability == .ready ? .paused : .unavailable
        wakeWordPauseReason = wakeWordCapability == .ready ? .system : nil
        wakeWordLogger.info("wake_word_paused reason=system_sleep")
    }

    private func suspendForUserSessionLock() async {
        guard userSessionAvailable else { return }
        userSessionAvailable = false
        voiceReplayBuffer.clear()
        userSessionExecutionGate.suspend()
        ownerPresenceLease.revoke()
        activeTranscriber?.cancel()
        wakeWordResumeTask?.cancel()
        wakeWordResumeTask = nil
        cancelWakeWordRecovery(resetGate: true)
        wakeWordDetector.stop()
        wakeWordListeningState = wakeWordCapability == .ready ? .paused : .unavailable
        wakeWordPauseReason = wakeWordCapability == .ready ? .session : nil
        speechOutput.stop()
        speechStreamChunker.reset()
        speechStreamOpen = false
        if let jobID = activeJobID, let secret = ipcSecret {
            _ = await Task.detached(priority: .userInitiated) {
                Self.cancelJob(jobID, secret: secret)
            }.value
        }
        activeJobID = nil
        activeComputerUseJobID = nil
        cancelApprovalExpiry()
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        voiceState = .idle
        wakeWordLogger.info("wake_word_paused reason=session_locked")
    }

    private func resumeAfterUserSessionUnlock() {
        guard !userSessionAvailable else { return }
        userSessionExecutionGate.resume()
        userSessionAvailable = true
        _ = ownerPresenceLease.authorize(at: ProcessInfo.processInfo.systemUptime)
        guard wakeWordMayResume else { return }
        wakeWordListeningState = .paused
        wakeWordPauseReason = .resuming
        wakeWordLogger.info("wake_word_resume_scheduled reason=session_unlocked")
        scheduleWakeWordResume(if: true)
    }

    private func resumeWakeWordAfterSystemWake() {
        wakeWordThermalAvailable = WakeWordThermalPolicy.allowsListening(
            ProcessInfo.processInfo.thermalState
        )
        wakeWordThermalStateLabel = AcousticThermalEvent(
            state: ProcessInfo.processInfo.thermalState
        ).label
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

    private func reconcileWakeWordThermalState(_ event: AcousticThermalEvent) {
        let previousAvailable = wakeWordThermalAvailable
        let previousLabel = wakeWordThermalStateLabel
        wakeWordThermalAvailable = event.allowsListening
        wakeWordThermalStateLabel = event.label
        if previousLabel != event.label, let secret = ipcSecret {
            let eventType = event.allowsListening ? "thermal_resume" : "thermal_pause"
            Task.detached(priority: .utility) {
                _ = Self.recordAcousticAuditEvent(
                    eventType,
                    data: ["state": event.label],
                    secret: secret
                )
            }
        }
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
        localOnly: Bool = false,
        completion: @escaping () -> Void
    ) {
        let isolatedCompletion = { [weak self] in
            completion()
            guard let self else { return }
            if !self.wakeWordDetector.isRunning {
                self.scheduleWakeWordResume(if: self.wakeWordOptedIn)
            }
        }
        if localOnly {
            speechOutput.speakLocally(text, completion: isolatedCompletion)
        } else {
            speechOutput.speak(
                text,
                ipcSecret: ipcSecret,
                completion: isolatedCompletion
            )
        }
        enableInterruptionListening()
    }

    private func trackSubmission(
        _ submission: SubmissionOutcome,
        conversationDecision: LocalVoiceConversationSession.Decision,
        secret: Data
    ) async {
        if
            conversationDecision.persistAcceptedConversation,
            let acceptedConversationID = submission.conversationID
        {
            let now = Date()
            conversationID = acceptedConversationID
            conversationLastUsedAt = now
            conversationSpeakerID = conversationDecision.boundSpeakerID
            conversationModelFingerprint = conversationDecision.boundModelFingerprint
            UserDefaults.standard.set(
                acceptedConversationID.uuidString.lowercased(),
                forKey: voiceConversationDefaultsKey
            )
            UserDefaults.standard.set(
                now.timeIntervalSince1970,
                forKey: voiceConversationLastUsedDefaultsKey
            )
            if let speakerID = conversationDecision.boundSpeakerID {
                UserDefaults.standard.set(speakerID, forKey: voiceConversationSpeakerDefaultsKey)
            } else {
                UserDefaults.standard.removeObject(forKey: voiceConversationSpeakerDefaultsKey)
            }
            if let modelFingerprint = conversationDecision.boundModelFingerprint {
                UserDefaults.standard.set(
                    modelFingerprint,
                    forKey: voiceConversationModelDefaultsKey
                )
            } else {
                UserDefaults.standard.removeObject(forKey: voiceConversationModelDefaultsKey)
            }
        }
        activeJobID = submission.jobID
        activeComputerUseJobID = nil
        voiceState = .processing
        speechStreamChunker.reset()
        speechStreamOpen = false
        enableInterruptionListening()
        let outcome = await awaitJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
    }

    private func voiceConversationDecision(
        for transcript: SpeechTranscriptEvent,
        now: Date = Date()
    ) -> LocalVoiceConversationSession.Decision {
        let decision = LocalVoiceConversationSession.decision(
            storedConversationID: conversationID,
            lastUsedAt: conversationLastUsedAt,
            storedSpeakerID: conversationSpeakerID,
            storedModelFingerprint: conversationModelFingerprint,
            currentSpeakerID: transcript.speakerID,
            currentModelFingerprint: speakerIdentityModelFingerprint,
            ownerSpeakerProfile: transcript.ownerSpeakerProfile,
            ownerPresenceVerified: transcript.ownerPresenceVerified,
            speakerIdentityReady: speakerIdentityCapability == .ready,
            now: now
        )
        if decision.discardStoredSession {
            clearVoiceConversationSession()
            logger.info("voice_conversation_reset source=invalid_or_expired")
        } else if !decision.persistAcceptedConversation {
            logger.info("voice_conversation_isolated reason=speaker_unverified")
        }
        return decision
    }

    private func clearVoiceConversationSession() {
        voiceReplayBuffer.clear()
        conversationID = nil
        conversationLastUsedAt = nil
        conversationSpeakerID = nil
        conversationModelFingerprint = nil
        UserDefaults.standard.removeObject(forKey: voiceConversationDefaultsKey)
        UserDefaults.standard.removeObject(forKey: voiceConversationLastUsedDefaultsKey)
        UserDefaults.standard.removeObject(forKey: voiceConversationSpeakerDefaultsKey)
        UserDefaults.standard.removeObject(forKey: voiceConversationModelDefaultsKey)
    }

    private func enableInterruptionListening() {
        guard wakeWordOptedIn, wakeWordCapability == .ready else { return }
        Task { @MainActor [weak self] in
            guard let self, !wakeWordDetector.isRunning else { return }
            await startWakeWordListening()
        }
    }

    private func consumeStreamSnapshot(_ text: String) -> Bool {
        let normalized = text.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        let chunks = speechStreamChunker.consume(normalized)
        guard !speechStreamChunker.isInvalid else { return false }
        guard !chunks.isEmpty else { return true }
        if !speechStreamOpen {
            speechStreamOpen = true
            voiceState = .speaking
            speechOutput.beginStream(ipcSecret: ipcSecret) { [weak self] in
                guard let self, voiceState == .speaking else { return }
                speechStreamOpen = false
                voiceState = .completed
                logger.info("voice_turn_completed mode=streaming")
            }
        }
        for chunk in chunks {
            speechOutput.enqueue(chunk)
        }
        return true
    }

    private func awaitJob(_ jobID: UUID, secret: Data) async -> JobOutcome {
        let startedAt = ProcessInfo.processInfo.systemUptime
        var observedStreamVersion = 0
        while ProcessInfo.processInfo.systemUptime - startedAt < 60 {
            guard activeJobID == jobID else { return .failed("job_superseded") }
            let afterStreamVersion = observedStreamVersion
            let status = await Task.detached(priority: .utility) { @Sendable in
                Self.waitForJobChange(
                    jobID,
                    afterStreamVersion: afterStreamVersion,
                    secret: secret
                )
            }.value
            guard activeJobID == jobID else { return .failed("job_superseded") }
            guard let status, status.jobID == jobID else {
                return .failed("job_status_unavailable")
            }
            if
                status.streamVersion > observedStreamVersion,
                let partial = status.partialResult
            {
                observedStreamVersion = status.streamVersion
                guard consumeStreamSnapshot(partial) else {
                    logger.error("voice_turn_failed stage=stream_integrity")
                    _ = await Task.detached(priority: .userInitiated) {
                        Self.cancelJob(jobID, secret: secret)
                    }.value
                    return .failed("job_stream_invalid")
                }
            }
            switch status.state {
            case .completed:
                lastEvaluation = status.evaluation
                if let evaluation = status.evaluation {
                    logger.info(
                        "voice_turn_evaluated brain=\(evaluation.brain.rawValue, privacy: .public) active_latency_ms=\(evaluation.totalLatencyMilliseconds, privacy: .public) wall_latency_ms=\(evaluation.wallLatencyMilliseconds ?? evaluation.totalLatencyMilliseconds, privacy: .public) confirmation_wait_ms=\(evaluation.confirmationWaitMilliseconds, privacy: .public) first_partial_ms=\(evaluation.firstPartialLatencyMilliseconds ?? -1, privacy: .public) chunks=\(evaluation.streamChunks, privacy: .public) verified=\(evaluation.outcomeVerified, privacy: .public) owner_verified=\(evaluation.ownerVerified, privacy: .public)"
                    )
                }
                guard let result = status.result else { return .failed("job_result_invalid") }
                return .completed(result)
            case .failed:
                lastEvaluation = status.evaluation
                return .failed(status.errorCode ?? "job_failed")
            case .cancelled:
                lastEvaluation = status.evaluation
                return .failed("job_cancelled")
            case .awaitingConfirmation:
                guard let confirmation = status.confirmation else {
                    return .failed("job_confirmation_invalid")
                }
                return .awaitingConfirmation(confirmation)
            case .queued, .running:
                continue
            }
        }
        return .failed("job_timeout")
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
            let response = try client.runtimePreflight()
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
            let providerEvent = IPCProviderStatusEvent(response: response)
            let provider = providerEvent
                .map { ProviderReadinessState(rawValue: $0.credential.rawValue) ?? .unavailable }
                ?? .unavailable
            guard let security = IPCSecurityStatusEvent(response: response) else {
                return ProbeResult(
                    state: state,
                    security: .compromised,
                    provider: provider,
                    localBrainAvailable: providerEvent?.localModel == .available,
                    secret: resolvedSecret
                )
            }
            return ProbeResult(
                state: state,
                security: security.integrity == .intact ? .intact : .compromised,
                provider: provider,
                localBrainAvailable: providerEvent?.localModel == .available,
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
        with transcriber: LocalSpeechTranscriber
    ) -> CaptureOutcome {
        do {
            guard let transcript = try transcriber.runForFinalTranscript(
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
        persistConversation: Bool,
        secret: Data
    ) -> SubmissionOutcome? {
        guard let client = try? LocalIPCClient(secret: secret) else {
            return nil
        }
        let response = switch request {
        case let .voice(transcript):
            try? client.submitVoiceTranscript(
                transcript,
                conversationID: conversationID,
                persistConversation: persistConversation
            )
        case let .image(image, transcript):
            try? client.submitImage(
                text: transcript.text,
                image: image,
                voiceContext: transcript,
                conversationID: conversationID,
                persistConversation: persistConversation
            )
        }
        guard
            let response,
            let submission = VoiceSubmissionEvent(response: response),
            !persistConversation || submission.conversationID != nil
        else {
            return nil
        }
        return SubmissionOutcome(
            jobID: submission.jobID,
            conversationID: submission.conversationID
        )
    }

    nonisolated private static func waitForJobChange(
        _ jobID: UUID,
        afterStreamVersion: Int,
        secret: Data
    ) -> IPCJobStatusEvent? {
        guard
            let response = try? LocalIPCClient(secret: secret).waitForJobChange(
                jobID,
                afterStreamVersion: afterStreamVersion
            )
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

    nonisolated private static func relayNextComputerCommand(
        secret: Data,
        executionGate: UserSessionExecutionGate,
        executionPermit: UserSessionExecutionGate.Permit
    ) -> Bool {
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
        let helperResponse = ComputerControlService.execute(
            command: command,
            executionGate: executionGate,
            executionPermit: executionPermit
        ) ?? [
            "status": "error",
            "reason": executionGate.isCurrent(executionPermit)
                ? "computer_helper_failed"
                : "user_session_inactive",
        ]
        if let pointerEvent = ComputerPointerEvent(
            command: command,
            response: helperResponse
        ) {
            DispatchQueue.main.async { @MainActor in
                if executionGate.isCurrent(executionPermit) {
                    JarvisPointerController.shared.present(pointerEvent, success: true)
                } else {
                    JarvisPointerController.shared.hide()
                }
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

    nonisolated private static func recordAcousticAuditEvent(
        _ eventType: String,
        data: [String: Any],
        secret: Data
    ) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).recordSystemAuditEvent(
                eventType,
                component: "acoustic_sensor",
                data: data
            ),
            response.ok,
            response.payload["recorded"] as? Bool == true
        else {
            return false
        }
        return true
    }

    private struct ProbeResult: Sendable {
        let state: DaemonConnectionState
        let security: SecurityMonitorState
        let provider: ProviderReadinessState
        let localBrainAvailable: Bool
        let secret: Data?

        init(
            state: DaemonConnectionState,
            security: SecurityMonitorState,
            provider: ProviderReadinessState = .unknown,
            localBrainAvailable: Bool = false,
            secret: Data?
        ) {
            self.state = state
            self.security = security
            self.provider = provider
            self.localBrainAvailable = localBrainAvailable
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
        let conversationID: UUID?
    }

    private enum SubmissionRequest: Sendable {
        case voice(SpeechTranscriptEvent)
        case image(LocalImageAttachment, transcript: SpeechTranscriptEvent)
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
        case cancelled
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
            case .cancelled:
                self = .cancelled
            }
        }
    }
}
