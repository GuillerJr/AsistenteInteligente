import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

let voiceConversationDefaultsKey = "ai.aegis.voice.conversation-id"
let voiceConversationLastUsedDefaultsKey = "ai.aegis.voice.conversation-last-used"
let voiceConversationSpeakerDefaultsKey = "ai.aegis.voice.conversation-speaker-id"
let voiceConversationModelDefaultsKey = "ai.aegis.voice.conversation-model-sha256"
let speakerOwnerDefaultsKey = "ai.aegis.voice.owner-speaker-id"
let speakerOwnerModelDefaultsKey = "ai.aegis.voice.owner-model-sha256"
let wakeWordOptInDefaultsKey = "ai.aegis.voice.wake-word-enabled"
let proactiveAlertsDefaultsKey = "ai.aegis.proactive-alerts-enabled"
let voiceApprovalDefaultsKey = "ai.aegis.voice.approval-enabled"
let screenCaptureRequestDefaultsKey = "ai.aegis.privacy.screen-requested"
let systemSettingsBundleIdentifier = "com.apple.systempreferences"

enum PrivacyRefreshTarget {
    case microphone
    case speech
    case screenCapture
    case computerControl
}

struct PendingBrowserSelection: Equatable, Sendable {
    let transcript: SpeechTranscriptEvent
    let options: [IPCBrowserOption]
}

enum VoiceInterruptionSource: String, Sendable {
    case hotKey = "hotkey"
    case wakeWord = "wake_word"
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
            String(localized: "sesión bloqueada", bundle: .module)
        case .runtime:
            "servicio no disponible"
        case .thermal:
            String(localized: "presión térmica", bundle: .module)
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
    var voiceOutputState = IPCSpeechOutputState.unavailable
    var localBrainAvailable = false
    var runtimeProbeInProgress = false
    var voiceState = VoiceTurnState.idle
    var lastVoiceFailureCode: String?
    var voiceOutputText: String?
    var voiceInputText: String?
    var mascotNotice: String?
    /// What waits for the owner (approvals, tasks, goals), shown by Dot in the notch.
    var pendingAttentionCount = 0
    var mascotCancellingCapture = false
    var mascotUsesVoice = false
    var voiceHistoryConversationID: UUID?
    @ObservationIgnored var mascotNoticeTask: Task<Void, Never>?
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
    /// Off unless the owner turns it on: with it off, an approval never opens the microphone.
    var voiceApprovalEnabled = UserDefaults.standard.bool(forKey: voiceApprovalDefaultsKey) {
        didSet { UserDefaults.standard.set(voiceApprovalEnabled, forKey: voiceApprovalDefaultsKey) }
    }
    var pendingApproval: PendingApproval?
    var pendingBrowserSelection: PendingBrowserSelection?
    var pendingInput: PendingInput?
    var pendingRecovery: PendingRecovery?
    var interactionSubmissionInProgress = false
    var activeComputerUseJobID: UUID?
    var approvalActionInProgress = false
    var lastEvaluation: IPCJobEvaluation?
    @ObservationIgnored var ipcSecret: Data?
    // Submission controls observe this gate directly. Ignoring it leaves a
    // child composer disabled when short-circuit evaluation stops reading
    // the remaining observable state during an active job.
    var activeJobID: UUID?
    @ObservationIgnored var conversationID = UserDefaults.standard
        .string(forKey: voiceConversationDefaultsKey)
        .flatMap(UUID.init(uuidString:))
    @ObservationIgnored var conversationLastUsedAt: Date? = {
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
    @ObservationIgnored var conversationSpeakerID: String? = {
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
    @ObservationIgnored var conversationModelFingerprint: String? = {
        guard
            let value = UserDefaults.standard.string(forKey: voiceConversationModelDefaultsKey),
            SpeakerIdentityCapability.isValidModelFingerprint(value)
        else {
            return nil
        }
        return value
    }()
    @ObservationIgnored var monitoring = false
    @ObservationIgnored var swarmMonitoring = false
    @ObservationIgnored var computerBridgeMonitoring = false
    @ObservationIgnored var scheduledDeliveriesMonitoring = false
    @ObservationIgnored var attentionMonitoring = false
    @ObservationIgnored let wakeWordDetector = WakeWordDetector()
    @ObservationIgnored let ownerPresenceAuthenticator = OwnerPresenceAuthenticator()
    @ObservationIgnored var ownerPresenceLease = OwnerPresenceLease()
    @ObservationIgnored let userSessionExecutionGate = UserSessionExecutionGate()
    var userSessionAvailable = true
    @ObservationIgnored var activeTranscriber: LocalSpeechTranscriber?
    @ObservationIgnored var pendingOwnerVoiceVariant: CapturedOwnerVoiceVariant?
    @ObservationIgnored let ownerVoiceVariantStore = AuthorizedOwnerVocalVariantStore()
    @ObservationIgnored let proactiveEventMonitor = ProactiveEventMonitor()
    @ObservationIgnored let thermalMonitor = ThermalMonitor()
    @ObservationIgnored var wakeWordResumeTask: Task<Void, Never>?
    @ObservationIgnored var wakeWordRecoveryTask: Task<Void, Never>?
    @ObservationIgnored var wakeWordStabilityTask: Task<Void, Never>?
    @ObservationIgnored var followUpWindowTask: Task<Void, Never>?
    @ObservationIgnored var followUpListeningGate = FollowUpListeningGate()
    @ObservationIgnored var latestAcousticVoiceActive = false
    @ObservationIgnored var wakeWordRecoveryGate = WakeWordRecoveryGate()
    @ObservationIgnored let runtimeStateSourceID = UUID()
    @ObservationIgnored var runtimeStateSequence = 0
    @ObservationIgnored var powerObservers: [any NSObjectProtocol] = []
    @ObservationIgnored var privacyObservers: [any NSObjectProtocol] = []
    @ObservationIgnored var privacyRefreshTask: Task<Void, Never>?
    @ObservationIgnored var approvalExpiryTask: Task<Void, Never>?
    @ObservationIgnored var dualAuthorizationTask: Task<Void, Never>?
    @ObservationIgnored var browserChoiceTask: Task<Void, Never>?
    @ObservationIgnored let dualChannelAuthorizer = DualChannelAuthorizer()
    @ObservationIgnored var privacyRelaunchPending = false
    @ObservationIgnored var privacyRelaunchInProgress = false
    @ObservationIgnored var wakeWordThermalAvailable = WakeWordThermalPolicy
        .allowsListening(ProcessInfo.processInfo.thermalState)
    @ObservationIgnored var wakeWordThermalStateLabel = AcousticThermalEvent(
        state: ProcessInfo.processInfo.thermalState
    ).label
    @ObservationIgnored var wakeWordEnergyAvailable = WakeWordEnergyPolicy
        .allowsListening(lowPowerModeEnabled: ProcessInfo.processInfo.isLowPowerModeEnabled)
    @ObservationIgnored let logger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "VoiceTurn"
    )
    @ObservationIgnored let securityLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "SecurityMonitor"
    )
    @ObservationIgnored let computerControlLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "ComputerControl"
    )
    @ObservationIgnored let privacyLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: String(localized: "Privacy", bundle: .module)
    )
    @ObservationIgnored let swarmLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "SwarmEvents"
    )
    @ObservationIgnored let wakeWordLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "WakeWord"
    )
    @ObservationIgnored let speechOutput = SpeechOutput()
    @ObservationIgnored let localVoiceTimer = LocalVoiceTimerScheduler()
    @ObservationIgnored var speechStreamChunker = SpeechStreamChunker()
    @ObservationIgnored var speechStreamOpen = false
    @ObservationIgnored var voiceReplayBuffer = LocalVoiceReplayBuffer()
    var isInterruptingSpeech = false
    let chat = JarvisChatStore()

    /// Meeting notes (Codex's Meetings): on-device transcription while recording, then the
    /// transcript becomes documents of a new chat that Jarvis summarizes; follow-up
    /// questions about the meeting work in that chat.
    var meetingRecording = false
    var meetingStartedAt: Date?
    @ObservationIgnored var meetingTranscriber: MeetingTranscriber?
    @ObservationIgnored var meetingResumesWakeWord = false

    /// Push-to-talk (Codex's hold-to-dictate): holding the voice key keeps the turn open
    /// through pauses, and releasing it ends the turn with everything that was said.
    @ObservationIgnored var pushToTalkHeld = false

    /// Messages written while Jarvis works, sent in order once it finishes (like Codex's
    /// queued prompts). Bounded and in memory; leaving the app drops them.
    var queuedChatMessages: [String] = []
    static let maximumQueuedChatMessages = 3
}
