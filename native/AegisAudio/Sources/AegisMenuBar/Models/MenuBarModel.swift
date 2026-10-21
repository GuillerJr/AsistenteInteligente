import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

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

struct PendingBrowserSelection: Equatable, Sendable {
    let transcript: SpeechTranscriptEvent
    let options: [IPCBrowserOption]
}

private enum VoiceInterruptionSource: String, Sendable {
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
    var lastVoiceFailureCode: String?
    var voiceOutputText: String?
    var voiceInputText: String?
    var mascotNotice: String?
    /// What waits for the owner (approvals, tasks, goals), shown by Dot in the notch.
    var pendingAttentionCount = 0
    var mascotCancellingCapture = false
    private var mascotUsesVoice = false
    private var voiceHistoryConversationID: UUID?
    @ObservationIgnored private var mascotNoticeTask: Task<Void, Never>?
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
    var pendingBrowserSelection: PendingBrowserSelection?
    var pendingInput: PendingInput?
    var pendingRecovery: PendingRecovery?
    var interactionSubmissionInProgress = false
    var activeComputerUseJobID: UUID?
    var approvalActionInProgress = false
    var lastEvaluation: IPCJobEvaluation?
    @ObservationIgnored private var ipcSecret: Data?
    // Submission controls observe this gate directly. Ignoring it leaves a
    // child composer disabled when short-circuit evaluation stops reading
    // the remaining observable state during an active job.
    private var activeJobID: UUID?
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
    @ObservationIgnored private var scheduledDeliveriesMonitoring = false
    @ObservationIgnored private var attentionMonitoring = false
    @ObservationIgnored private let wakeWordDetector = WakeWordDetector()
    @ObservationIgnored private let ownerPresenceAuthenticator = OwnerPresenceAuthenticator()
    @ObservationIgnored private var ownerPresenceLease = OwnerPresenceLease()
    @ObservationIgnored private let userSessionExecutionGate = UserSessionExecutionGate()
    private var userSessionAvailable = true
    @ObservationIgnored private var activeTranscriber: LocalSpeechTranscriber?
    @ObservationIgnored private var pendingOwnerVoiceVariant: CapturedOwnerVoiceVariant?
    @ObservationIgnored private let ownerVoiceVariantStore = AuthorizedOwnerVocalVariantStore()
    @ObservationIgnored private let proactiveEventMonitor = ProactiveEventMonitor()
    @ObservationIgnored private let thermalMonitor = ThermalMonitor()
    @ObservationIgnored private var wakeWordResumeTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordRecoveryTask: Task<Void, Never>?
    @ObservationIgnored private var wakeWordStabilityTask: Task<Void, Never>?
    @ObservationIgnored private var followUpWindowTask: Task<Void, Never>?
    @ObservationIgnored private var followUpListeningGate = FollowUpListeningGate()
    @ObservationIgnored private var latestAcousticVoiceActive = false
    @ObservationIgnored private var wakeWordRecoveryGate = WakeWordRecoveryGate()
    @ObservationIgnored private let runtimeStateSourceID = UUID()
    @ObservationIgnored private var runtimeStateSequence = 0
    @ObservationIgnored private var powerObservers: [any NSObjectProtocol] = []
    @ObservationIgnored private var privacyObservers: [any NSObjectProtocol] = []
    @ObservationIgnored private var privacyRefreshTask: Task<Void, Never>?
    @ObservationIgnored private var approvalExpiryTask: Task<Void, Never>?
    @ObservationIgnored private var dualAuthorizationTask: Task<Void, Never>?
    @ObservationIgnored private var browserChoiceTask: Task<Void, Never>?
    @ObservationIgnored private let dualChannelAuthorizer = DualChannelAuthorizer()
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
    var isInterruptingSpeech = false
    let chat = JarvisChatStore()

    var mascotSessionAvailable: Bool { userSessionAvailable }

    var canPlanMascotIntent: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact
            && providerState == .configured && activeJobID == nil && !chat.isBusy
            && !voiceState.isBusy && interactionID == nil && ipcSecret != nil
    }

    /// Companion traffic uses independent, short IPC calls and never chat errors/jobs.
    func performMascotIPC(_ method: String, payload: [String: Any]) async -> Data? {
        guard userSessionAvailable, let permit = userSessionExecutionGate.permit,
              let secret = ipcSecret,
              let encoded = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        let result = await Task.detached(priority: .utility) { () -> Data? in
            guard let decoded = try? JSONSerialization.jsonObject(with: encoded) as? [String: Any],
                  let response = try? LocalIPCClient(secret: secret, timeoutSeconds: 2)
                    .call(method: method, payload: decoded), response.ok else { return nil }
            return try? JSONSerialization.data(withJSONObject: response.payload)
        }.value
        guard userSessionExecutionGate.isCurrent(permit), !Task.isCancelled else { return nil }
        return result
    }

    var canActivateMascotVoice: Bool {
        userSessionAvailable && !chat.isEditing && pendingApproval == nil
            && pendingBrowserSelection == nil && pendingRecovery == nil
            && (canStartVoiceTurn || voiceState == .speaking || voiceState == .processing
                || voiceState == .followingUp)
    }

    func cancelMascotListening() {
        guard userSessionAvailable, voiceState == .listening || voiceState == .followingUp,
              !mascotCancellingCapture else { return }
        cancelFollowUpWindow()
        if let activeTranscriber {
            mascotCancellingCapture = true
            activeTranscriber.cancel()
        } else {
            voiceState = .idle
            scheduleWakeWordResume(if: wakeWordOptedIn)
        }
    }

    private func configureMascotAlerts() {
        proactiveEventMonitor.onAlert = { [weak self] message in
            guard let self, userSessionAvailable else { return }
            mascotNoticeTask?.cancel()
            mascotNotice = String(message.prefix(512))
            mascotNoticeTask = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                self?.mascotNotice = nil
                self?.mascotNoticeTask = nil
            }
        }
    }

    var canSendChatMessage: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact
            && hybridBrainReady && activeJobID == nil && pendingApproval == nil
            && pendingInput == nil && !voiceState.isBusy && !chat.isBusy
            && !speakerModelTrainingState.isBusy && !chat.selectionLoading && !chat.importing
    }

    var canManageChatHistory: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact
            && activeJobID == nil && !chat.isEditing && !chat.historyLoading && !voiceState.isBusy
            && pendingApproval == nil && pendingInput == nil
    }

    var canReadChatHistory: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact && ipcSecret != nil
    }

    func performChatIPC(_ method: String, payload: [String: Any] = [:]) async -> Data? {
        guard userSessionAvailable, let permit = userSessionExecutionGate.permit,
              let secret = ipcSecret,
              let encoded = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        let result = await Task.detached(priority: .userInitiated) { () -> (Data?, String?) in
            do {
                let decoded = try JSONSerialization.jsonObject(with: encoded) as? [String: Any] ?? [:]
                let response = try LocalIPCClient(secret: secret).call(method: method, payload: decoded)
                guard response.ok else { return (nil, response.errorCode) }
                return (try JSONSerialization.data(withJSONObject: response.payload), nil)
            } catch { return (nil, "daemon_unavailable") }
        }.value
        guard userSessionExecutionGate.isCurrent(permit), !Task.isCancelled else { return nil }
        if result.0 == nil {
            switch result.1 {
            case "conversation_not_found", "telegram_conversation_not_found":
                chat.error = "Esa conversación ya no está disponible. Actualiza el historial."
            case "telegram_owner_not_linked":
                chat.error = "Vincula y habilita tu cuenta en Configuración → Telegram."
            case "telegram_conversation_busy":
                chat.error = "Telegram tiene una tarea activa. Termínala o cancélala antes de cambiar de chat."
            default:
                chat.error = "No pude completar la operación. Inténtalo de nuevo."
            }
        }
        return result.0
    }

    /// Meeting notes (Codex's Meetings): on-device transcription while recording, then the
    /// transcript becomes documents of a new chat that Jarvis summarizes; follow-up
    /// questions about the meeting work in that chat.
    var meetingRecording = false
    var meetingStartedAt: Date?
    @ObservationIgnored private var meetingTranscriber: MeetingTranscriber?
    @ObservationIgnored private var meetingResumesWakeWord = false

    func toggleMeetingNotes() async {
        if let transcriber = meetingTranscriber {
            meetingTranscriber = nil
            let transcript = await transcriber.stop()
            meetingRecording = false
            let started = meetingStartedAt ?? Date()
            meetingStartedAt = nil
            scheduleWakeWordResume(if: meetingResumesWakeWord)
            guard transcript.count >= 20 else {
                chat.notice = "No escuché nada que guardar de la reunión."
                return
            }
            let formatter = DateFormatter()
            formatter.dateFormat = "dd/MM HH:mm"
            let stamp = formatter.string(from: started)
            chat.newConversation()
            chat.attachments = ChatAttachment.meetingTranscript(transcript, title: "Reunión \(stamp)")
            logger.info("meeting_notes_submitted")
            _ = await sendChatMessage(
                "Estas son las notas de mi reunión del \(stamp). Resúmela: temas, decisiones, "
                + "tareas con su responsable y fecha si aparecen, y lo que quedó pendiente."
            )
            return
        }
        guard userSessionAvailable, !voiceState.isBusy, !chat.isBusy else { return }
        guard let transcriber = MeetingTranscriber() else {
            chat.error = "No puedo transcribir en este Mac: revisa los permisos de micrófono y "
                + "reconocimiento de voz."
            return
        }
        meetingResumesWakeWord = pauseWakeWordListening()
        do {
            try transcriber.start()
        } catch {
            scheduleWakeWordResume(if: meetingResumesWakeWord)
            chat.error = "No pude empezar a grabar la reunión."
            return
        }
        meetingTranscriber = transcriber
        meetingStartedAt = Date()
        meetingRecording = true
        logger.info("meeting_notes_started")
    }

    var meetingLiveText: String { meetingTranscriber?.text ?? "" }

    /// Push-to-talk (Codex's hold-to-dictate): holding the voice key keeps the turn open
    /// through pauses, and releasing it ends the turn with everything that was said.
    @ObservationIgnored private var pushToTalkHeld = false

    func voiceHoldStarted() {
        pushToTalkHeld = true
        activeTranscriber?.setHeld(true)
    }

    func voiceHoldEnded() {
        pushToTalkHeld = false
        activeTranscriber?.setHeld(false)
        activeTranscriber?.finishNow()
    }

    /// Messages written while Jarvis works, sent in order once it finishes (like Codex's
    /// queued prompts). Bounded and in memory; leaving the app drops them.
    var queuedChatMessages: [String] = []
    static let maximumQueuedChatMessages = 3

    var canQueueChatMessage: Bool {
        chat.transcript.jobID != nil && queuedChatMessages.count < Self.maximumQueuedChatMessages
    }

    @discardableResult
    func queueChatMessage(_ text: String) -> Bool {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canQueueChatMessage, !cleaned.isEmpty, cleaned.utf8.count <= 4_096 else { return false }
        queuedChatMessages.append(cleaned)
        logger.info("chat_message_queued count=\(self.queuedChatMessages.count, privacy: .public)")
        return true
    }

    func removeQueuedChatMessage(at index: Int) {
        guard queuedChatMessages.indices.contains(index) else { return }
        queuedChatMessages.remove(at: index)
    }

    /// Redirect the running request (Codex's "steer"): stop it and ask again with the
    /// owner's correction attached, so Jarvis does not finish the wrong thing first.
    @discardableResult
    func steerChatResponse(_ correction: String) async -> Bool {
        let cleaned = correction.trimmingCharacters(in: .whitespacesAndNewlines)
        guard chat.transcript.jobID != nil, !cleaned.isEmpty,
              let original = chat.transcript.messages.last(where: { $0.role == .user })?.text
        else { return false }
        await cancelChatResponse()
        guard chat.transcript.jobID == nil else { return false }
        let combined = String("\(original)\n\n(Corrección: \(cleaned))".prefix(4_000))
        logger.info("chat_response_steered")
        return await sendChatMessage(combined)
    }

    private func sendNextQueuedChatMessage() async {
        guard !queuedChatMessages.isEmpty, canSendChatMessage else { return }
        let next = queuedChatMessages.removeFirst()
        if !(await sendChatMessage(next)) { queuedChatMessages.insert(next, at: 0) }
    }

    @discardableResult
    func sendChatMessage(_ text: String, maximumBytes: Int = 4_096) async -> Bool {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let text = cleaned.isEmpty && !chat.attachments.isEmpty ? "Analiza los archivos adjuntos." : cleaned
        guard canSendChatMessage, !text.isEmpty, text.utf8.count <= maximumBytes,
              let permit = userSessionExecutionGate.permit, let secret = ipcSecret else { return false }
        let conversationID = chat.conversationID
        let revision = chat.transcriptRevision
        if chat.transcript.hasNewerHistory
            || chat.transcript.messages.count + 2 > TextChatTranscript.maximumVisibleMessages {
            guard await chat.loadRecent(model: self), canSendChatMessage,
                  chat.conversationID == conversationID, chat.transcriptRevision == revision,
                  userSessionExecutionGate.isCurrent(permit) else { return false }
        }
        chat.submitting = true
        defer { chat.submitting = false }
        chat.transcriptRevision = UUID()
        mascotUsesVoice = false
        voiceInputText = nil
        voiceOutputText = nil
        voiceHistoryConversationID = nil
        mascotNotice = nil
        let attachments = chat.attachments
        chat.error = nil
        guard chat.transcript.begin(text, attachmentNames: attachments.map(\.name)) else { return false }
        cancelFollowUpWindow()
        speechOutput.stop()
        speechStreamChunker.reset()
        speechStreamOpen = false
        pendingRecovery = nil
        lastVoiceFailureCode = nil
        _ = pauseWakeWordListening()
        voiceState = .submitting
        let existingConversation = chat.conversationID
        let temporaryChat = chat.temporary
        let result = await Task.detached(priority: .userInitiated) { () -> (UUID?, VoiceSubmissionEvent?, String?) in
            do {
                let client = try LocalIPCClient(secret: secret)
                var conversation = existingConversation
                if conversation == nil {
                    let title = String(text.replacingOccurrences(of: "\n", with: " ").prefix(80))
                    let created = try client.call(
                        method: "conversations.create",
                        payload: temporaryChat ? ["title": "Chat temporal", "temporary": true]
                                               : ["title": title]
                    )
                    guard created.ok, let raw = created.payload["conversation_id"] as? String,
                          let parsed = UUID(uuidString: raw) else {
                        return (nil, nil, created.errorCode ?? "conversation_unavailable")
                    }
                    conversation = parsed
                }
                guard let conversation else { return (nil, nil, "conversation_unavailable") }
                var payload: [String: Any] = [
                    "text": text, "conversation_id": conversation.uuidString.lowercased()
                ]
                let documents = attachments.compactMap(\.document)
                if !documents.isEmpty {
                    payload["documents"] = documents.map { ["name": $0.name, "content": $0.content] }
                }
                if let attachedImage = attachments.first(where: \.isImage), let image = attachedImage.image {
                    payload["image"] = ["media_type": image.mediaType, "data_base64": image.data.base64EncodedString()]
                    payload["image_name"] = attachedImage.name
                }
                let response = try client.call(method: payload["image"] == nil ? "swarm.submit" : "image.submit",
                                               payload: payload)
                return (conversation, VoiceSubmissionEvent(response: response),
                        response.ok ? nil : response.errorCode ?? "job_submission_failed")
            } catch { return (existingConversation, nil, "daemon_unavailable") }
        }.value
        guard userSessionExecutionGate.isCurrent(permit) else {
            if let submission = result.1 {
                _ = await Task.detached(priority: .utility) { Self.cancelJob(submission.jobID, secret: secret) }.value
            }
            chat.transcript.finish("La sesión se bloqueó. La solicitud se canceló.", interrupted: true)
            voiceState = .idle
            return true
        }
        chat.conversationID = result.0
        if let created = result.0, temporaryChat { chat.adoptTemporaryConversation(created) }
        guard let submission = result.1 else {
            chat.transcript.finish(LocalVoiceTurnFeedback.spokenJobFailure(for: result.2 ?? "job_submission_failed")
                                   ?? "No pude completar la solicitud. Inténtalo de nuevo.",
                                   interrupted: true)
            voiceState = .idle
            scheduleWakeWordResume(if: wakeWordOptedIn)
            return true
        }
        chat.transcript.attach(submission.jobID)
        chat.attachments = []
        activeJobID = submission.jobID
        voiceState = .processing
        let outcome = await awaitJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
        Task { await sendNextQueuedChatMessage() }
        return true
    }

    func cancelChatResponse() async {
        guard !chat.cancelling, userSessionAvailable, let jobID = chat.transcript.jobID,
              activeJobID == jobID, let secret = ipcSecret else { return }
        chat.cancelling = true
        defer { chat.cancelling = false }
        if pendingApproval?.jobID == jobID { _ = await denyPending(); return }
        let cancelled = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(jobID, secret: secret)
        }.value
        guard cancelled, activeJobID == jobID else { return }
        chat.transcript.finish("Respuesta cancelada.", jobID: jobID, interrupted: true)
        activeJobID = nil
        activeComputerUseJobID = nil
        voiceState = .idle
        scheduleWakeWordResume(if: wakeWordOptedIn)
    }

    var canStartVoiceTurn: Bool {
        !meetingRecording
            && userSessionAvailable
            && daemonState == .online
            && securityState == .intact
            && hybridBrainReady
            && microphonePermission == .authorized
            && speechPermission == .authorized
            && !voiceState.isBusy
            && !chat.isBusy && !chat.selectionLoading && !chat.importing
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
        providerState == .configured
    }

    var voiceFailureTitle: String {
        LocalVoiceTurnFeedback.shortJobFailureTitle(for: lastVoiceFailureCode)
    }

    private var wakeWordMayResume: Bool {
        userSessionAvailable
            && wakeWordOptedIn
            && wakeWordCapability == .ready
            && speakerIdentityCapability == .ready
            && effectiveSpeakerOwnerIdentifier != nil
            && speakerIdentityModelFingerprint != nil
            && microphonePermission == .authorized
            && wakeWordRuntimeAvailable
            && wakeWordThermalAvailable
            && wakeWordEnergyAvailable
            && (wakeWordListeningState == .paused
                || wakeWordListeningState == .unavailable)
    }

    func startPowerMonitoring() {
        guard powerObservers.isEmpty else { return }
        thermalMonitor.start { [weak self] snapshot in
            self?.synchronizeDaemonRuntimeState(
                trigger: snapshot.transitionCause,
                snapshot: snapshot
            )
        }
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
        configureMascotAlerts()
        guard proactiveAlertsEnabled else { return }
        proactiveAlertsEnabled = await proactiveEventMonitor.setEnabled(true)
        UserDefaults.standard.set(
            proactiveAlertsEnabled,
            forKey: proactiveAlertsDefaultsKey
        )
    }

    func initializeOperationalEvidence() async {
        do {
            _ = try await JarvisOperationalEvidenceRecorder.shared.current()
        } catch {
            logger.error("operational_evidence_unavailable")
        }
    }

    func registerBundledDaemonLaunchFailure() {
        daemonState = .securityFailure
        securityState = .unavailable
        voiceState = .failed
        securityLogger.fault("bundled_daemon_launch_failed")
    }

    func setProactiveAlertsEnabled(_ enabled: Bool) async {
        configureMascotAlerts()
        if !enabled {
            mascotNoticeTask?.cancel()
            mascotNoticeTask = nil
            mascotNotice = nil
        }
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
        async let scheduled: Void = monitorScheduledDeliveries()
        async let attention: Void = monitorAttention()
        _ = await (runtime, swarm, computer, scheduled, attention)
    }

    /// Dot reacts when something new needs the owner: an approval waiting in Telegram,
    /// a scheduled task that could not finish, a blocked goal. It only reads the list.
    private func monitorAttention() async {
        guard !attentionMonitoring else { return }
        attentionMonitoring = true
        defer { attentionMonitoring = false }
        while !Task.isCancelled {
            if userSessionAvailable, daemonState == .online, securityState == .intact,
               let ipcSecret {
                let entries = await Task.detached(priority: .utility) {
                    Self.attentionEntries(secret: ipcSecret)
                }.value
                if let entries {
                    let grew = entries.count > pendingAttentionCount
                    pendingAttentionCount = entries.count
                    chat.attention = entries
                    if grew { announcePending(entries) }
                }
            }
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
        }
    }

    private func announcePending(_ entries: [JarvisChatStore.AttentionEntry]) {
        let approvals = entries.filter { $0.action == "review_approval" }.count
        let message = approvals > 0
            ? (approvals == 1 ? "Una acción espera tu aprobación." : "\(approvals) acciones esperan tu aprobación.")
            : (entries.count == 1 ? "Hay algo pendiente para ti." : "Hay \(entries.count) cosas pendientes para ti.")
        mascotNoticeTask?.cancel()
        mascotNotice = message
        mascotNoticeTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(12)) } catch { return }
            self?.mascotNotice = nil
            self?.mascotNoticeTask = nil
        }
    }

    /// The health panel's report, read without touching the chat's error banner.
    func healthReport() async -> JarvisHealthReport? {
        guard userSessionAvailable, let ipcSecret else { return nil }
        return await Task.detached(priority: .userInitiated) {
            guard let response = try? LocalIPCClient(secret: ipcSecret).call(
                method: "health.status", payload: [:]), response.ok,
                  let data = try? JSONSerialization.data(withJSONObject: response.payload)
            else { return nil }
            return try? JSONDecoder().decode(JarvisHealthReport.self, from: data)
        }.value
    }

    nonisolated private static func attentionEntries(secret: Data) -> [JarvisChatStore.AttentionEntry]? {
        guard let response = try? LocalIPCClient(secret: secret).call(method: "attention.list", payload: [:]),
              response.ok, let items = response.payload["items"] as? [[String: Any]]
        else { return nil }
        return JarvisChatStore.attentionEntries(items)
    }

    /// Results of scheduled tasks ("cada mañana resúmeme mis correos") become Mac
    /// notifications; the full result is in the task's own conversation.
    private func monitorScheduledDeliveries() async {
        guard !scheduledDeliveriesMonitoring else { return }
        scheduledDeliveriesMonitoring = true
        defer { scheduledDeliveriesMonitoring = false }
        // Only results delivered after launch: older ones were already shown or sent.
        var since = ISO8601DateFormatter().string(from: Date())
        var authorizationRequested = false
        while !Task.isCancelled {
            if userSessionAvailable, daemonState == .online, securityState == .intact,
               let ipcSecret {
                let cursor = since
                let deliveries = await Task.detached(priority: .utility) {
                    Self.scheduledDeliveries(since: cursor, secret: ipcSecret)
                }.value
                if !deliveries.isEmpty, !authorizationRequested {
                    authorizationRequested = true
                    _ = try? await UNUserNotificationCenter.current()
                        .requestAuthorization(options: [.alert, .sound])
                }
                for delivery in deliveries {
                    since = max(since, delivery.deliveredAt)
                    let content = UNMutableNotificationContent()
                    content.title = "Jarvis · \(delivery.title)"
                    content.body = String(delivery.text.prefix(600))
                    content.sound = .default
                    content.threadIdentifier = "ai.aegis.scheduled"
                    try? await UNUserNotificationCenter.current().add(UNNotificationRequest(
                        identifier: "ai.aegis.scheduled.\(delivery.id)",
                        content: content, trigger: nil
                    ))
                    logger.info("scheduled_delivery_notified")
                }
            }
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
        }
    }

    private struct ScheduledDeliveryNotice: Sendable {
        let id: String
        let title: String
        let text: String
        let deliveredAt: String
    }

    nonisolated private static func scheduledDeliveries(
        since: String,
        secret: Data
    ) -> [ScheduledDeliveryNotice] {
        guard
            let response = try? LocalIPCClient(secret: secret).call(
                method: "schedules.deliveries", payload: ["since": since]
            ),
            response.ok,
            let items = response.payload["deliveries"] as? [[String: Any]]
        else { return [] }
        return items.compactMap { item in
            guard let id = item["delivery_id"] as? String,
                  let title = item["title"] as? String,
                  let text = item["text"] as? String,
                  let deliveredAt = item["delivered_at"] as? String
            else { return nil }
            return ScheduledDeliveryNotice(
                id: id, title: String(title.prefix(80)), text: text, deliveredAt: deliveredAt
            )
        }
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
        let previousDaemonState = daemonState
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
        persistReadinessSnapshot()
        let currentPower = ThermalMonitor.currentSnapshot()
        if
            result.state == .online,
            result.security == .intact,
            previousDaemonState != .online || !result.runtimePowerStateKnown
                || result.runtimePower?.matches(
                    thermalState: currentPower.thermalState,
                    lowPowerMode: currentPower.lowPowerMode,
                    powerSource: currentPower.powerSource
                ) != true
        {
            // Reconcile a missed power notification or failed IPC delivery using
            // a fresh OS observation; never infer recovery from daemon connectivity.
            synchronizeDaemonRuntimeState(trigger: currentPower.transitionCause, snapshot: currentPower)
        }
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
        persistReadinessSnapshot()
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
        persistReadinessSnapshot()
        guard
            wakeWordCapability == .ready,
            speakerIdentityCapability == .ready,
            effectiveSpeakerOwnerIdentifier != nil,
            speakerIdentityModelFingerprint != nil
        else {
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
        defer { persistReadinessSnapshot() }
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
        await refreshSpeakerIdentityConfiguration()
        guard
            wakeWordCapability == .ready,
            speakerIdentityCapability == .ready,
            effectiveSpeakerOwnerIdentifier != nil,
            speakerIdentityModelFingerprint != nil
        else {
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
        let shouldRestartWakeWord = wakeWordDetector.isRunning
        if shouldRestartWakeWord {
            wakeWordDetector.stop()
            wakeWordListeningState = .paused
            wakeWordPauseReason = .resuming
            scheduleWakeWordResume(if: effectiveSpeakerOwnerIdentifier != nil)
        }
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
        persistReadinessSnapshot()
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
        persistReadinessSnapshot()
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
        guard !chat.isBusy else { return }
        if voiceState == .speaking || voiceState == .processing || voiceState == .submitting {
            await interruptCurrentTurnAndListen(source: .hotKey)
            return
        }
        if voiceState == .followingUp {
            cancelFollowUpWindow()
            voiceState = .idle
        }
        await performVoiceTurn(playCue: true)
    }

    private func performVoiceTurn(
        playCue: Bool,
        admittedFromFollowUp: Bool = false
    ) async {
        let stateAllowsStart = admittedFromFollowUp
            ? voiceState == .listening
            : !voiceState.isBusy
        guard stateAllowsStart, pendingApproval == nil else {
            return
        }
        cancelFollowUpWindow()
        voiceActivityLevel = 0
        lastVoiceFailureCode = nil
        pendingRecovery = nil
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
            announceVoiceFailure(
                preflightFailureMessage(),
                code: "voice_preflight_unavailable"
            )
            return
        }

        logger.info("voice_turn_started")
        let capture = await captureSpokenPrompt(playCue: playCue)
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
                    LocalVoiceTurnFeedback.spokenCaptureFailure(for: reason.rawValue),
                    code: reason.rawValue
                )
            }
            return
        }
        lastSpeakerID = transcript.speakerID
        if pendingInput != nil || pendingBrowserSelection != nil {
            guard let trusted = await transcriptWithOwnerPresence(transcript),
                  trusted.ownerSpeakerProfile, trusted.ownerPresenceVerified, userSessionAvailable else {
                speakLocalVoiceUtility("No pude verificar al propietario. Responde desde el diálogo de Jarvis.",
                                      utility: "conversation", event: "unverified")
                return
            }
        }
        if pendingInput != nil {
            await answerPendingInput(transcript.text)
            return
        }
        if pendingBrowserSelection != nil {
            if let selection = matchingBrowserOption(for: transcript.text) {
                await selectBrowser(bundleIdentifier: selection.bundleIdentifier)
            } else if ["cancelar", "cancela", "cancel"].contains(
                transcript.text.folding(
                    options: [.caseInsensitive, .diacriticInsensitive],
                    locale: Locale(identifier: "es")
                ).lowercased()
            ) {
                browserChoiceTask?.cancel()
                browserChoiceTask = nil
                pendingBrowserSelection = nil
                voiceState = .idle
            } else {
                voiceState = .awaitingAuthorization
            }
            return
        }
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
        lastVoiceFailureCode = nil
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartVoiceTurn, let secret = ipcSecret else {
            logger.error("image_turn_failed stage=preflight")
            announceVoiceFailure(
                preflightFailureMessage(),
                code: "voice_preflight_unavailable"
            )
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
            announceVoiceFailure(
                "No pude leer esa imagen de forma segura.",
                code: "image_encoding_failed"
            )
            return
        }

        await startVisualVoiceTurn(image, secret: secret)
    }

    func startScreenVoiceTurn() async {
        guard !voiceState.isBusy, pendingApproval == nil else {
            return
        }
        voiceActivityLevel = 0
        lastVoiceFailureCode = nil
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard canStartScreenTurn, let secret = ipcSecret else {
            logger.error("screen_turn_failed stage=preflight")
            announceVoiceFailure(
                preflightFailureMessage(screenCaptureRequired: true),
                code: "voice_preflight_unavailable"
            )
            return
        }

        voiceState = .submitting
        guard let allowedBundleIdentifier = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        else {
            logger.error("screen_turn_failed stage=target")
            announceVoiceFailure(
                "No pude identificar una ventana autorizada.",
                code: "screen_target_unavailable"
            )
            return
        }
        let image: LocalImageAttachment
        let captureStartedAt = ProcessInfo.processInfo.systemUptime
        do {
            image = try await ScreenCaptureService.captureAuthorizedFrontmostWindow(
                allowedBundleIdentifier: allowedBundleIdentifier
            )
            let captureMilliseconds = max(
                0,
                Int(
                    ((ProcessInfo.processInfo.systemUptime - captureStartedAt) * 1_000)
                        .rounded()
                )
            )
            Task.detached(priority: .utility) {
                try? await JarvisOperationalEvidenceRecorder.shared.recordScreenCapture(
                    milliseconds: captureMilliseconds,
                    payloadBytes: image.data.count
                )
            }
        } catch ScreenCaptureServiceError.tccPermissionDenied(let permission) {
            screenCaptureAuthorized = ScreenCaptureService.isAuthorized
            let jobID = activeJobID
            let acknowledged = await Task.detached(priority: .userInitiated) {
                Self.reportTCCPermissionDenied(
                    permission: permission,
                    operation: .screenTurn,
                    jobID: jobID,
                    secret: secret
                )
            }.value
            privacyLogger.error(
                "tcc_permission_denied permission=\(permission.rawValue, privacy: .public) ipc_acknowledged=\(acknowledged)"
            )
            announceVoiceFailure(
                "El permiso de privacidad necesario fue revocado. No reintentaré esta acción.",
                code: "tcc_permission_denied"
            )
            return
        } catch {
            logger.error("screen_turn_failed stage=capture")
            announceVoiceFailure(
                "No pude capturar la pantalla.",
                code: "screen_capture_failed"
            )
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
                    LocalVoiceTurnFeedback.spokenCaptureFailure(for: reason.rawValue),
                    code: reason.rawValue
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
            announceVoiceFailure(
                "No pude comunicarme con el servicio local.",
                code: "daemon_unavailable"
            )
            return
        }
        logger.info("visual_voice_turn_submitted")
        await trackSubmission(
            submission,
            conversationDecision: conversationDecision,
            secret: secret
        )
    }

    func approvePending(expected: PendingApproval? = nil, always: Bool = false) async {
        guard
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit,
            !approvalActionInProgress,
            let pendingApproval,
            expected == nil || pendingApproval == expected,
            !always || pendingApproval.confirmation.standingGrantAvailable,
            let secret = ipcSecret
        else {
            return
        }
        cancelDualAuthorization()
        approvalActionInProgress = true
        defer { approvalActionInProgress = false }
        logger.info(
            "tool_confirmation_approved tool=\(pendingApproval.confirmation.toolName, privacy: .public)"
        )
        let accepted = await Task.detached(priority: .userInitiated) {
            Self.approveJob(pendingApproval, always: always, secret: secret)
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
        if pendingRecovery?.code == "correction_not_applied" { pendingRecovery = nil }
        cancelApprovalExpiry()
        self.pendingApproval = nil
        activeJobID = pendingApproval.jobID
        voiceState = .processing
        let outcome = await awaitJob(pendingApproval.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: pendingApproval.jobID)
        Task { await sendNextQueuedChatMessage() }
    }

    @discardableResult
    func denyPending(expected: PendingApproval? = nil) async -> Bool {
        guard
            userSessionAvailable,
            let sessionPermit = userSessionExecutionGate.permit,
            !approvalActionInProgress,
            let pendingApproval,
            expected == nil || pendingApproval == expected,
            let secret = ipcSecret
        else {
            return false
        }
        cancelDualAuthorization()
        approvalActionInProgress = true
        defer { approvalActionInProgress = false }
        let denied = await Task.detached(priority: .userInitiated) {
            Self.cancelJob(pendingApproval.jobID, secret: secret)
        }.value
        guard userSessionExecutionGate.isCurrent(sessionPermit) else { return false }
        guard denied else {
            logger.error("tool_confirmation_failed stage=deny")
            return false
        }
        logger.info(
            "tool_confirmation_denied tool=\(pendingApproval.confirmation.toolName, privacy: .public)"
        )
        if pendingRecovery?.code == "correction_not_applied" { pendingRecovery = nil }
        cancelApprovalExpiry()
        self.pendingApproval = nil
        if activeJobID == pendingApproval.jobID {
            activeJobID = nil
        }
        if activeComputerUseJobID == pendingApproval.jobID {
            activeComputerUseJobID = nil
        }
        JarvisPointerController.shared.hide()
        if chat.transcript.jobID == pendingApproval.jobID {
            chat.transcript.finish("No ejecuté la acción.", jobID: pendingApproval.jobID, interrupted: true)
            voiceState = .completed
            scheduleWakeWordResume(if: wakeWordOptedIn)
            return true
        }
        voiceState = .speaking
        speakWithWakeWordIsolation("Entendido. No ejecuté la acción.") { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
        }
        return true
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
        if chat.transcript.jobID == jobID {
            chat.transcript.finish("Control del Mac cancelado.", jobID: jobID, interrupted: true)
        }
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
            cancelDualAuthorization()
            JarvisPointerController.shared.hide()
            if chat.transcript.jobID == jobID {
                chat.inputJobID = pendingInput?.id
                chat.transcript.finish(result, jobID: jobID)
                Task { await chat.refreshHistory(model: self) }
                voiceState = .completed
                scheduleWakeWordResume(if: wakeWordOptedIn)
            } else {
                if let id = voiceHistoryConversationID {
                    Task { await chat.refreshVoiceTranscript(conversationID: id, model: self) }
                }
                speakCompletedResult(result)
            }
        case let .awaitingConfirmation(confirmation):
            let approval = PendingApproval(jobID: jobID, confirmation: confirmation)
            pendingApproval = approval
            activeComputerUseJobID = confirmation.toolName == "computer_use" ? jobID : nil
            voiceState = .awaitingAuthorization
            scheduleApprovalExpiry(for: approval)
            logger.info(
                "tool_confirmation_requested tool=\(confirmation.toolName, privacy: .public)"
            )
            beginDualAuthorization(for: approval)
        case let .failed(errorCode):
            activeJobID = nil
            activeComputerUseJobID = nil
            cancelApprovalExpiry()
            cancelDualAuthorization()
            pendingApproval = nil
            if speechStreamOpen {
                speechOutput.stop()
                speechStreamChunker.reset()
                speechStreamOpen = false
            }
            JarvisPointerController.shared.hide()
            if chat.transcript.jobID == jobID {
                let message = errorCode == "job_cancelled" ? "Respuesta cancelada."
                    : LocalVoiceTurnFeedback.spokenJobFailure(for: errorCode)
                        ?? "No pude completar la solicitud. Inténtalo de nuevo."
                chat.transcript.finish(message,
                                       jobID: jobID, interrupted: true)
                voiceState = .idle
                scheduleWakeWordResume(if: wakeWordOptedIn)
                return
            }
            logger.error(
                "voice_turn_failed stage=job reason=\(errorCode, privacy: .public)"
            )
            announceVoiceFailure(
                LocalVoiceTurnFeedback.spokenJobFailure(for: errorCode),
                code: errorCode
            )
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

    private func beginDualAuthorization(for approval: PendingApproval) {
        cancelDualAuthorization()
        guard
            userSessionAvailable,
            let secret = ipcSecret
        else {
            return
        }
        let ownerIdentifier = effectiveSpeakerOwnerIdentifier
        let modelFingerprint = speakerIdentityModelFingerprint
        dualAuthorizationTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let result = await dualChannelAuthorizer.authorize(
                selectedOwnerIdentifier: ownerIdentifier,
                expectedModelFingerprint: modelFingerprint,
                physicalApproval: {
                    await Task.detached(priority: .userInitiated) {
                        Self.approveJob(approval, secret: secret)
                    }.value
                },
                voiceApproval: { evidence in
                    await Task.detached(priority: .userInitiated) {
                        Self.approveJobByVoice(
                            approval,
                            evidence: evidence,
                            secret: secret
                        )
                    }.value
                }
            )
            guard
                !Task.isCancelled,
                userSessionAvailable,
                pendingApproval == approval,
                activeJobID == approval.jobID
            else {
                return
            }
            dualAuthorizationTask = nil
            switch result {
            case .authorizedByTouchID, .authorizedByVoice:
                cancelApprovalExpiry()
                pendingApproval = nil
                withAnimation(.easeOut(duration: 0.15)) {
                    voiceState = .processing
                }
                logger.info(
                    "tool_confirmation_authorized channel=\(String(describing: result), privacy: .public)"
                )
                let outcome = await awaitJob(approval.jobID, secret: secret)
                handleJobOutcome(outcome, jobID: approval.jobID)
            case .manualConfirmationRequired:
                withAnimation(.easeOut(duration: 0.15)) {
                    voiceState = .awaitingAuthorization
                }
            }
        }
    }

    private func cancelDualAuthorization() {
        dualAuthorizationTask?.cancel()
        dualAuthorizationTask = nil
        dualChannelAuthorizer.cancel()
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

    private func announceVoiceFailure(_ response: String?, code: String? = nil) {
        lastVoiceFailureCode = code
        if let response, let code, !["job_cancelled", "job_superseded"].contains(code) {
            pendingRecovery = PendingRecovery(code: code, message: response)
        }
        speechOutput.stop()
        voiceOutputText = response.map { String($0.prefix(2_000)) }
        if speechStreamOpen {
            speechStreamChunker.reset()
            speechStreamOpen = false
        }
        guard response != nil else {
            voiceState = .idle
            return
        }
        // Failure feedback remains local, immediate and silent; it is not a replay request.
        voiceState = .failed
        scheduleWakeWordResume(if: wakeWordOptedIn)
    }

    private func speakCompletedResult(_ result: String) {
        lastVoiceFailureCode = nil
        let normalized = result.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        voiceOutputText = String(normalized.prefix(2_000))
        if speechStreamOpen {
            let chunks = speechStreamChunker.finish(normalized)
            guard !speechStreamChunker.isInvalid else {
                logger.error("voice_turn_failed stage=stream_integrity")
                announceVoiceFailure(
                    LocalVoiceTurnFeedback.spokenJobFailure(for: "job_stream_invalid"),
                    code: "job_stream_invalid"
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
                LocalVoiceTurnFeedback.spokenJobFailure(for: "empty_agent_response"),
                code: "empty_agent_response"
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
            self?.completeConversationalPlayback(mode: "single_response")
        }
    }

    private func submitVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        secret: Data
    ) async {
        if requiresBrowserResolution(transcript.text) {
            guard let resolution = await resolveBrowserTarget(for: transcript, secret: secret) else {
                logger.error("browser_discovery_failed")
                announceVoiceFailure(
                    "No pude verificar qué navegadores están disponibles; no ejecuté la búsqueda.",
                    code: "browser_discovery_failed"
                )
                return
            }
            if resolution.requiresSelection {
                presentBrowserSelection(
                    transcript: transcript,
                    options: resolution.browsers
                )
                return
            }
            await submitResolvedVoiceTranscript(
                transcript,
                preferredBrowserBundleIdentifier: resolution.selectedBundleIdentifier,
                secret: secret
            )
            return
        }
        await submitResolvedVoiceTranscript(
            transcript,
            preferredBrowserBundleIdentifier: nil,
            secret: secret
        )
    }

    private func submitResolvedVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        preferredBrowserBundleIdentifier: String?,
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
        let activeApplicationBundleIdentifier = NSWorkspace.shared.frontmostApplication?
            .bundleIdentifier
        let submission = await Task.detached(priority: .utility) {
            Self.submitRequest(
                .voice(
                    trustedTranscript,
                    preferredBrowserBundleIdentifier: preferredBrowserBundleIdentifier,
                    activeApplicationBundleIdentifier: activeApplicationBundleIdentifier
                ),
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
            announceVoiceFailure(
                "No pude comunicarme con el servicio local.",
                code: "daemon_unavailable"
            )
            return
        }
        logger.info("voice_turn_submitted")
        await trackSubmission(
            submission,
            conversationDecision: conversationDecision,
            secret: secret
        )
    }

    private func resolveBrowserTarget(
        for transcript: SpeechTranscriptEvent,
        secret: Data
    ) async -> IPCBrowserDiscovery? {
        guard requiresBrowserResolution(transcript.text) else { return nil }
        return await Task.detached(priority: .utility) {
            guard
                let response = try? LocalIPCClient(secret: secret).discoverBrowsers(
                    for: transcript.text
                )
            else {
                return nil
            }
            return IPCBrowserDiscovery(response: response)
        }.value
    }

    private func requiresBrowserResolution(_ text: String) -> Bool {
        let normalized = text.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es")
        ).lowercased()
        return normalized.range(
            of: #"(?:^|\s)(?:el\s+navegador|the\s+browser|navegador|browser)(?:\s|$)"#,
            options: .regularExpression
        ) != nil
    }

    private func presentBrowserSelection(
        transcript: SpeechTranscriptEvent,
        options: [IPCBrowserOption]
    ) {
        let running = options.filter(\.running)
        let candidates = running.isEmpty ? options : running
        guard candidates.count > 1 else { return }
        browserChoiceTask?.cancel()
        pendingBrowserSelection = PendingBrowserSelection(
            transcript: transcript,
            options: candidates
        )
        voiceState = .speaking
        // Only this public prompt reaches TTS. Installed browser names stay in the local HUD.
        speakWithWakeWordIsolation(
            "¿Qué navegador quieres usar? Di su nombre o elígelo en pantalla."
        ) { [weak self] in
            guard let self, pendingBrowserSelection != nil else { return }
            browserChoiceTask = Task { @MainActor [weak self] in
                await self?.captureSpokenBrowserChoice()
            }
        }
    }

    private func captureSpokenBrowserChoice() async {
        guard pendingBrowserSelection != nil else { return }
        let capture = await captureSpokenPrompt(
            playCue: false,
            durationSeconds: 8
        )
        guard
            !Task.isCancelled,
            case let .transcript(transcript) = capture,
            let selection = matchingBrowserOption(for: transcript.text)
        else {
            if pendingBrowserSelection != nil {
                voiceState = .awaitingAuthorization
            }
            browserChoiceTask = nil
            return
        }
        browserChoiceTask = nil
        guard let trusted = await transcriptWithOwnerPresence(transcript),
              trusted.ownerSpeakerProfile, trusted.ownerPresenceVerified, userSessionAvailable else {
            if pendingBrowserSelection != nil { voiceState = .awaitingAuthorization }
            return
        }
        await selectBrowser(bundleIdentifier: selection.bundleIdentifier)
    }

    private func matchingBrowserOption(for spokenText: String) -> IPCBrowserOption? {
        guard let pendingBrowserSelection else { return nil }
        let normalized = spokenText.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es")
        ).split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        return pendingBrowserSelection.options.first { option in
            let name = option.name.folding(
                options: [.caseInsensitive, .diacriticInsensitive],
                locale: Locale(identifier: "es")
            ).lowercased()
            return normalized.lowercased() == name
                || (name == "chrome" && normalized.lowercased() == "google chrome")
        }
    }

    func selectBrowser(bundleIdentifier: String) async {
        guard
            let selection = pendingBrowserSelection,
            let option = selection.options.first(where: {
                $0.bundleIdentifier == bundleIdentifier
            }),
            let secret = ipcSecret
        else {
            return
        }
        browserChoiceTask?.cancel()
        browserChoiceTask = nil
        pendingBrowserSelection = nil
        logger.info(
            "browser_choice_selected browser=\(option.name, privacy: .public)"
        )
        await submitResolvedVoiceTranscript(
            selection.transcript,
            preferredBrowserBundleIdentifier: option.bundleIdentifier,
            secret: secret
        )
    }

    func pauseApprovalForCorrection() {
        cancelDualAuthorization()
    }

    func resumeApprovalAfterCorrection(_ approval: PendingApproval) {
        guard pendingApproval == approval, userSessionAvailable,
              !approvalActionInProgress, !interactionSubmissionInProgress,
              pendingRecovery?.code != "correction_not_applied",
              approval.confirmation.expiresAt > Date() else { return }
        beginDualAuthorization(for: approval)
    }

    func correctPendingRequest(_ text: String, expectedApproval: PendingApproval) async {
        guard pendingApproval == expectedApproval, !interactionSubmissionInProgress,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= 4_096 else { return }
        let toChat = chat.transcript.jobID == expectedApproval.jobID
        guard await denyPending(expected: expectedApproval) else {
            guard userSessionAvailable, pendingApproval == expectedApproval else { return }
            cancelDualAuthorization()
            pendingRecovery = PendingRecovery(
                code: "correction_not_applied",
                message: "No pude confirmar la cancelación de la acción anterior. La corrección no se envió; revisa el resultado antes de solicitar otro cambio."
            )
            return
        }
        guard pendingApproval == nil else { return }
        await submitInteractionText(text, toChat: toChat)
    }

    func answerPendingInput(_ answer: String, expectedID: UUID? = nil) async {
        guard let input = pendingInput, input.request.expiresAt > Date(),
              expectedID == nil || input.id == expectedID,
              !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              answer.utf8.count <= 4_096 else { return }
        let text = "Solicitud del usuario: \(input.request.requestText)\n"
            + "Pregunta de aclaración: \(input.request.question)\n"
            + "Información adicional del usuario: \(answer)"
        await submitInteractionText(text, toChat: chat.inputJobID == input.id)
    }

    private func submitInteractionText(_ text: String, toChat: Bool = false) async {
        if toChat, !chat.isBusy, pendingApproval == nil, activeJobID == nil {
            pendingInput = nil
            await sendChatMessage(text, maximumBytes: 16_000)
            return
        }
        guard userSessionAvailable, let permit = userSessionExecutionGate.permit,
              pendingApproval == nil, activeJobID == nil,
              !interactionSubmissionInProgress, let secret = ipcSecret,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= 16_000 else { return }
        interactionSubmissionInProgress = true
        defer { interactionSubmissionInProgress = false }
        cancelFollowUpWindow()
        speechOutput.stop()
        pendingInput = nil
        pendingRecovery = nil
        voiceState = .submitting
        let currentConversation = voiceHistoryConversationID ?? chat.conversationID
        mascotUsesVoice = true
        voiceInputText = String(text.prefix(4_096))
        voiceOutputText = nil
        let response = await Task.detached(priority: .utility) { () -> (SubmissionOutcome?, String?) in
            var payload: [String: Any] = ["text": text]
            if let currentConversation { payload["conversation_id"] = currentConversation.uuidString.lowercased() }
            guard let client = try? LocalIPCClient(secret: secret),
                  let response = try? client.call(method: "swarm.submit", payload: payload)
            else { return (nil, "daemon_unavailable") }
            guard response.ok else { return (nil, response.errorCode ?? "job_submission_failed") }
            guard let submission = VoiceSubmissionEvent(response: response)
            else { return (nil, "job_result_invalid") }
            return (SubmissionOutcome(jobID: submission.jobID, conversationID: submission.conversationID), nil)
        }.value
        let submission = response.0
        guard userSessionExecutionGate.isCurrent(permit) else {
            if let submission {
                _ = await Task.detached(priority: .utility) { Self.cancelJob(submission.jobID, secret: secret) }.value
            }
            voiceState = .idle
            return
        }
        guard let submission else {
            let code = response.1 ?? "daemon_unavailable"
            let message = code == "daemon_unavailable"
                ? "No pude comunicarme con el servicio local."
                : LocalVoiceTurnFeedback.spokenJobFailure(for: code)
            announceVoiceFailure(message, code: code)
            return
        }
        if let currentConversation {
            voiceHistoryConversationID = currentConversation
        }
        activeJobID = submission.jobID
        voiceState = .processing
        speechStreamChunker.reset()
        speechStreamOpen = false
        let outcome = await awaitJob(submission.jobID, secret: secret)
        handleJobOutcome(outcome, jobID: submission.jobID)
    }

    func cancelInteraction(expectedID: String? = nil) async {
        guard expectedID == nil || interactionID == expectedID else { return }
        if pendingApproval != nil {
            await denyPending()
            return
        }
        if pendingBrowserSelection != nil {
            browserChoiceTask?.cancel()
            browserChoiceTask = nil
            speechOutput.stop()
            pendingBrowserSelection = nil
            voiceState = .idle
        }
        if pendingInput != nil {
            cancelFollowUpWindow()
            speechOutput.stop()
            pendingInput = nil
            voiceState = .idle
        }
        pendingRecovery = nil
    }

    func expireInput(_ id: UUID) {
        guard pendingInput?.id == id else { return }
        pendingInput = nil
        if !voiceState.isBusy { voiceState = .idle }
    }

    private func transcriptWithOwnerPresence(
        _ transcript: SpeechTranscriptEvent
    ) async -> SpeechTranscriptEvent? {
        var presenceVerified = false
        var manuallyAuthorizedThisTurn = false
        if transcript.ownerSpeakerProfile, userSessionAvailable {
            let uptime = ProcessInfo.processInfo.systemUptime
            if ownerPresenceLease.isAuthorized(at: uptime) {
                presenceVerified = true
            } else if await ownerPresenceAuthenticator.verify(), userSessionAvailable {
                presenceVerified = ownerPresenceLease.authorize(
                    at: ProcessInfo.processInfo.systemUptime
                )
                manuallyAuthorizedThisTurn = presenceVerified
            }
            logger.info(
                "owner_presence_checked verified=\(presenceVerified, privacy: .public)"
            )
        }
        if manuallyAuthorizedThisTurn,
           let variant = pendingOwnerVoiceVariant,
           variant.captureID == transcript.captureID
        {
            pendingOwnerVoiceVariant = nil
            let store = ownerVoiceVariantStore
            let persisted = await Task.detached(priority: .background) {
                try? store.persist(variant)
            }.value
            if persisted != nil, let secret = ipcSecret {
                let notified = await Task.detached(priority: .background) {
                    guard let client = try? LocalIPCClient(secret: secret) else {
                        return false
                    }
                    return (
                        try? client.notifyBiometricTrainingSampleReady(
                            captureID: variant.captureID
                        ).ok
                    ) == true
                }.value
                logger.info(
                    "owner_voice_variant_notified accepted=\(notified, privacy: .public)"
                )
            }
            logger.info(
                "owner_voice_variant_validated persisted=\(persisted != nil, privacy: .public)"
            )
        } else if pendingOwnerVoiceVariant?.captureID == transcript.captureID {
            pendingOwnerVoiceVariant = nil
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
        guard event == "replayed" else {
            announceVoiceFailure(
                text,
                code: event == "unverified"
                    ? "owner_verification_required" : "speech_replay_unavailable"
            )
            return
        }
        voiceState = .speaking
        speakWithWakeWordIsolation(text, replayOnly: true) { [weak self] in
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
        if trustedTranscript.ownerSpeakerProfile && trustedTranscript.ownerPresenceVerified {
            chat.newConversation()
        }
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

    private func captureSpokenPrompt(
        playCue: Bool = true,
        durationSeconds: TimeInterval = VoiceCapturePolicy.normalTurnMaximumSeconds
    ) async -> CaptureOutcome {
        let shouldResumeWakeWord = pauseWakeWordListening()
        defer { scheduleWakeWordResume(if: shouldResumeWakeWord) }
        voiceState = .listening
        mascotUsesVoice = true
        voiceInputText = nil
        voiceOutputText = nil
        mascotNotice = nil
        chat.error = nil
        let activityHandler: @Sendable (Float) -> Void = { [weak self] level in
            Task { @MainActor [weak self] in
                self?.voiceActivityLevel = level
            }
        }
        let selectedOwnerIdentifier = effectiveSpeakerOwnerIdentifier
        pendingOwnerVoiceVariant = nil
        let speakerModelFingerprint = speakerIdentityModelFingerprint
        let transcriber = LocalSpeechTranscriber(
            writer: NDJSONWriter(handle: .nullDevice),
            activityHandler: activityHandler,
            captureReadyHandler: {
                guard playCue else { return }
                Task { @MainActor in NSSound.beep() }
            },
            selectedOwnerIdentifier: selectedOwnerIdentifier,
            expectedSpeakerModelFingerprint: speakerModelFingerprint
        )
        activeTranscriber = transcriber
        if pushToTalkHeld { transcriber.setHeld(true) }
        defer {
            if activeTranscriber === transcriber {
                activeTranscriber = nil
            }
        }
        let capture = await Task.detached(priority: .userInitiated) {
            Self.captureTranscript(
                with: transcriber,
                durationSeconds: durationSeconds
            )
        }.value
        pendingOwnerVoiceVariant = transcriber.capturedOwnerVoiceVariant
        voiceActivityLevel = 0
        if mascotCancellingCapture, activeTranscriber === transcriber {
            mascotCancellingCapture = false
            pendingOwnerVoiceVariant = nil
            voiceState = .idle
            scheduleWakeWordResume(if: wakeWordOptedIn)
            return .cancelled
        }
        if userSessionAvailable, case let .transcript(transcript) = capture {
            voiceInputText = String(transcript.text.prefix(4_096))
        }
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
            speakerIdentityCapability == .ready,
            microphonePermission == .authorized,
            !wakeWordEnrollmentState.isBusy,
            let ownerIdentifier = effectiveSpeakerOwnerIdentifier,
            let modelFingerprint = speakerIdentityModelFingerprint
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
        let activityHandler: @Sendable (AcousticActivityEvent) -> Void = { [weak self] event in
            Task { @MainActor [weak self] in
                self?.handleAcousticActivity(event)
            }
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
                    selectedOwnerIdentifier: ownerIdentifier,
                    expectedSpeakerModelFingerprint: modelFingerprint,
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
        try? await JarvisOperationalEvidenceRecorder.shared.recordWakeWordDetection()
        wakeWordLogger.info("wake_word_detected")
        if voiceState == .speaking || voiceState == .processing || voiceState == .submitting {
            await interruptCurrentTurnAndListen(source: .wakeWord)
            return
        }
        if pendingBrowserSelection != nil {
            await performVoiceTurn(playCue: false)
            return
        }
        guard canStartVoiceTurn, voiceState != .awaitingAuthorization else { return }
        let shouldResume = pauseWakeWordListening()
        await startVoiceTurn()
        scheduleWakeWordResume(if: shouldResume)
    }

    private func interruptCurrentTurnAndListen(source: VoiceInterruptionSource) async {
        guard !chat.isBusy else { return }
        wakeWordLogger.info(
            "voice_turn_interrupted source=\(source.rawValue, privacy: .public)"
        )
        cancelFollowUpWindow()
        wakeWordDetector.stop()
        wakeWordListeningState = .paused
        wakeWordPauseReason = .audio
        let interruptedJobID = activeJobID
        activeJobID = nil
        activeComputerUseJobID = nil
        cancelApprovalExpiry()
        cancelDualAuthorization()
        pendingApproval = nil
        JarvisPointerController.shared.hide()
        speechStreamChunker.reset()
        speechStreamOpen = false
        guard let secret = ipcSecret else {
            isInterruptingSpeech = speechOutput.hasAudiblePlayback
            await speechOutput.interruptWithFade()
            isInterruptingSpeech = false
            wakeWordLogger.error("voice_turn_interruption_failed stage=ipc_auth")
            voiceState = .failed
            return
        }

        let cancellationTask = Task.detached(priority: .userInitiated) {
            guard let jobID = interruptedJobID else { return true }
            return Self.cancelJob(jobID, secret: secret)
        }
        let fadesAudio = speechOutput.hasAudiblePlayback
        isInterruptingSpeech = fadesAudio
        await speechOutput.interruptWithFade()
        isInterruptingSpeech = false
        let jobCancellationSucceeded = await cancellationTask.value
        let auditSucceeded = await Task.detached(priority: .utility) {
            Self.recordAcousticAuditEvent(
                "voice_interruption",
                data: [
                    "job_present": interruptedJobID != nil,
                    "job_cancelled": jobCancellationSucceeded,
                    "source": source.rawValue,
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
        try? await JarvisOperationalEvidenceRecorder.shared.recordSuccessfulInterruption()
        voiceState = .idle
        await performVoiceTurn(playCue: false)
    }

    private func suspendWakeWordForSystemSleep() {
        voiceReplayBuffer.clear()
        speechOutput.stop()
        speechOutput.clearReplay()
        voiceOutputText = nil
        cancelFollowUpWindow()
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
        voiceInputText = nil
        mascotNoticeTask?.cancel()
        mascotNoticeTask = nil
        mascotNotice = nil
        if let jobID = chat.transcript.jobID {
            chat.transcript.finish("Solicitud detenida al bloquear la sesión.", jobID: jobID, interrupted: true)
        }
        pendingInput = nil
        pendingRecovery = nil
        pendingBrowserSelection = nil
        browserChoiceTask?.cancel()
        browserChoiceTask = nil
        voiceReplayBuffer.clear()
        speechOutput.clearReplay()
        voiceOutputText = nil
        cancelFollowUpWindow()
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
        cancelDualAuthorization()
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

    private func synchronizeDaemonRuntimeState(
        trigger: AudioRuntimeTransitionCause,
        snapshot: ThermalBudgetSnapshot? = nil
    ) {
        let current = snapshot ?? ThermalMonitor.currentSnapshot()
        guard
            daemonState == .online,
            securityState == .intact,
            let ipcSecret,
            runtimeStateSequence < 9_007_199_254_740_991
        else {
            return
        }
        let thermalState = current.thermalState
        let lowPowerMode = current.lowPowerMode
        let effectiveCause: AudioRuntimeTransitionCause
        if !thermalState.allowsFullRuntime {
            effectiveCause = .thermalThrottle
        } else if lowPowerMode {
            effectiveCause = .lowPowerMode
        } else if trigger == .lowPowerDisabled {
            effectiveCause = .lowPowerDisabled
        } else {
            effectiveCause = .thermalRecovery
        }
        runtimeStateSequence += 1
        let sequence = runtimeStateSequence
        let sourceID = runtimeStateSourceID
        Task.detached(priority: .utility) {
            _ = try? LocalIPCClient(secret: ipcSecret).updateAudioRuntimeState(
                sourceID: sourceID,
                sequence: sequence,
                cause: effectiveCause,
                thermalState: thermalState,
                lowPowerMode: lowPowerMode,
                powerSource: current.powerSource
            )
        }
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
                    || meetingRecording
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
        replayOnly: Bool = false,
        completion: @escaping () -> Void
    ) {
        // Bind diagnostics to this playback; a later timer/replay must not inherit an old error.
        lastVoiceFailureCode = nil
        voiceOutputText = String(text.prefix(2_000))
        let isolatedCompletion: (SpeechPlaybackOutcome) -> Void = { [weak self] outcome in
            guard let self else { return }
            if outcome == .completed {
                completion()
            } else {
                self.lastVoiceFailureCode = outcome.failureCode
                self.voiceState = .failed
                self.logger.error("voice_playback_incomplete reason=\(outcome.failureCode ?? "unknown", privacy: .public)")
            }
            if !self.wakeWordDetector.isRunning {
                self.scheduleWakeWordResume(if: self.wakeWordOptedIn)
            }
        }
        if replayOnly {
            speechOutput.replayLocally(text, completion: isolatedCompletion)
        } else {
            speechOutput.speak(
                text,
                ipcSecret: ipcSecret,
                completion: isolatedCompletion
            )
        }
        if speechOutput.isActive {
            enableInterruptionListening()
        }
    }

    private func trackSubmission(
        _ submission: SubmissionOutcome,
        conversationDecision: LocalVoiceConversationSession.Decision,
        secret: Data
    ) async {
        voiceHistoryConversationID = nil
        if
            conversationDecision.persistAcceptedConversation,
            let acceptedConversationID = submission.conversationID
        {
            let now = Date()
            conversationID = acceptedConversationID
            conversationLastUsedAt = now
            conversationSpeakerID = conversationDecision.boundSpeakerID
            conversationModelFingerprint = conversationDecision.boundModelFingerprint
            if conversationDecision.boundSpeakerID != nil,
               conversationDecision.boundModelFingerprint != nil {
                voiceHistoryConversationID = acceptedConversationID
                chat.conversationID = acceptedConversationID
                chat.transcriptRevision = UUID()
            } else {
                voiceHistoryConversationID = nil
            }
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
            now: now,
            useActiveConversation: chat.conversationID != nil || chat.hasExplicitConversationSelection,
            activeConversationID: chat.conversationID
        )
        if decision.discardStoredSession {
            let capturedInput = voiceInputText
            clearVoiceConversationSession()
            voiceInputText = capturedInput
            logger.info("voice_conversation_reset source=invalid_or_expired")
        } else if !decision.persistAcceptedConversation {
            logger.info("voice_conversation_isolated reason=speaker_unverified")
        }
        return decision
    }

    private func clearVoiceConversationSession() {
        voiceReplayBuffer.clear()
        speechOutput.clearReplay()
        voiceOutputText = nil
        voiceInputText = nil
        voiceHistoryConversationID = nil
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

    private func completeConversationalPlayback(mode: String) {
        speechStreamOpen = false
        Task.detached(priority: .utility) {
            try? await JarvisOperationalEvidenceRecorder.shared.recordCompletedPlayback()
        }
        logger.info("voice_turn_completed mode=\(mode, privacy: .public)")
        armFollowUpWindow()
    }

    private func armFollowUpWindow() {
        cancelFollowUpWindow()
        guard
            userSessionAvailable,
            pendingApproval == nil,
            pendingInput == nil,
            wakeWordOptedIn,
            wakeWordCapability == .ready,
            microphonePermission == .authorized,
            speechPermission == .authorized,
            wakeWordThermalAvailable,
            wakeWordEnergyAvailable
        else {
            voiceState = .completed
            return
        }

        followUpListeningGate.arm(
            at: ProcessInfo.processInfo.systemUptime,
            voiceIsActive: latestAcousticVoiceActive
        )
        voiceState = .followingUp
        voiceActivityLevel = latestAcousticVoiceActive ? 0.12 : 0.18
        logger.info("follow_up_window_armed duration_ms=5000")
        followUpWindowTask = Task { @MainActor [weak self] in
            guard let self else { return }
            if !wakeWordDetector.isRunning {
                await startWakeWordListening()
            }
            guard
                !Task.isCancelled,
                wakeWordDetector.isRunning,
                voiceState == .followingUp
            else {
                followUpListeningGate.cancel()
                if voiceState == .followingUp {
                    voiceState = .completed
                }
                followUpWindowTask = nil
                return
            }
            do {
                try await Task.sleep(for: .seconds(5))
            } catch {
                return
            }
            guard
                !Task.isCancelled,
                voiceState == .followingUp,
                followUpListeningGate.expire(
                    at: ProcessInfo.processInfo.systemUptime
                ) == .expire
            else { return }
            followUpWindowTask = nil
            voiceActivityLevel = 0
            voiceState = .idle
            logger.info("follow_up_window_expired mode=wake_word_only")
        }
    }

    private func cancelFollowUpWindow() {
        followUpWindowTask?.cancel()
        followUpWindowTask = nil
        followUpListeningGate.cancel()
    }

    private func handleAcousticActivity(_ event: AcousticActivityEvent) {
        latestAcousticVoiceActive = event.voiceActive
        guard voiceState == .followingUp else { return }
        voiceActivityLevel = event.voiceActive ? 0.72 : 0.18
        guard
            followUpListeningGate.observe(
                voiceIsActive: event.voiceActive,
                at: ProcessInfo.processInfo.systemUptime
            ) == .beginCapture
        else { return }
        followUpWindowTask?.cancel()
        followUpWindowTask = nil
        voiceState = .listening
        Task.detached(priority: .utility) {
            try? await JarvisOperationalEvidenceRecorder.shared.recordFollowUpVoiceTurn()
        }
        logger.info("follow_up_voice_detected source=adaptive_noise_floor")
        Task { @MainActor [weak self] in
            await self?.performVoiceTurn(
                playCue: false,
                admittedFromFollowUp: true
            )
        }
    }

    private func consumeStreamSnapshot(_ text: String) -> Bool {
        let normalized = text.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        let chunks = speechStreamChunker.consume(normalized)
        guard !speechStreamChunker.isInvalid else { return false }
        voiceOutputText = String(normalized.prefix(2_000))
        guard !chunks.isEmpty else { return true }
        if !speechStreamOpen {
            speechStreamOpen = true
            voiceState = .speaking
            speechOutput.beginStream(ipcSecret: ipcSecret) { [weak self] outcome in
                guard let self, voiceState == .speaking else { return }
                if outcome == .completed {
                    completeConversationalPlayback(mode: "streaming")
                } else {
                    lastVoiceFailureCode = outcome.failureCode
                    voiceState = .failed
                    scheduleWakeWordResume(if: wakeWordOptedIn)
                }
            }
        }
        for chunk in chunks {
            speechOutput.enqueue(chunk)
        }
        return true
    }

    private func awaitJob(_ jobID: UUID, secret: Data) async -> JobOutcome {
        let startedAt = ProcessInfo.processInfo.systemUptime
        var monitoring = JobMonitoringDeadline(startedAt: startedAt)
        var observedStreamVersion = 0
        while !monitoring.isExpired(at: ProcessInfo.processInfo.systemUptime) {
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
            monitoring.observe(status, at: ProcessInfo.processInfo.systemUptime)
            if
                status.streamVersion > observedStreamVersion,
                let partial = status.partialResult
            {
                observedStreamVersion = status.streamVersion
                let valid = chat.transcript.jobID == jobID
                    ? chat.transcript.update(partial, jobID: jobID)
                    : consumeStreamSnapshot(partial)
                guard valid else {
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
                    if evaluation.voiceRequest {
                        Task.detached(priority: .utility) {
                            try? await JarvisOperationalEvidenceRecorder.shared.recordVoiceTurn(
                                ownerVerified: evaluation.ownerVerified,
                                firstPartialMilliseconds:
                                    evaluation.firstPartialLatencyMilliseconds,
                                totalMilliseconds: evaluation.totalLatencyMilliseconds
                            )
                        }
                    }
                }
                guard let result = status.result else { return .failed("job_result_invalid") }
                if let request = status.inputRequest, request.expiresAt > Date() {
                    pendingInput = PendingInput(id: jobID, request: request)
                }
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
        if activeJobID == jobID {
            _ = await Task.detached(priority: .userInitiated) { Self.cancelJob(jobID, secret: secret) }.value
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
        persistReadinessSnapshot()
    }

    private func persistReadinessSnapshot() {
        do {
            try JarvisReadinessStore.persist(
                JarvisReadinessSnapshot(
                    daemon: daemonReadinessValue,
                    security: securityState.rawValue,
                    provider: providerState.rawValue,
                    localBrainAvailable: localBrainAvailable,
                    microphone: microphonePermission.rawValue,
                    speechRecognition: speechPermission.rawValue,
                    screenCaptureAuthorized: screenCaptureAuthorized,
                    computerControl: computerControlReadinessValue,
                    wakeWord: wakeWordCapability.rawValue,
                    wakeWordEnabled: wakeWordOptedIn,
                    speakerIdentity: speakerIdentityCapability.rawValue
                )
            )
        } catch {
            privacyLogger.error("readiness_snapshot_write_failed")
        }
    }

    private var daemonReadinessValue: String {
        switch daemonState {
        case .unknown: "unknown"
        case .checking: "checking"
        case .online: "online"
        case .offline: "offline"
        case .securityFailure: "security_failure"
        }
    }

    private var computerControlReadinessValue: String {
        switch computerControlCapability {
        case .ready: "ready"
        case .screenCaptureMissing: "screen_capture_missing"
        case .accessibilityMissing: "accessibility_missing"
        case .permissionsMissing: "permissions_missing"
        case .helperUnavailable: "helper_unavailable"
        }
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
            if !response.ok, response.errorCode == "security_compromised" {
                let securityResponse = try client.securityStatus()
                let compromised = IPCSecurityStatusEvent(response: securityResponse)?.integrity
                    == .compromised
                return ProbeResult(
                    state: compromised ? .online : .securityFailure,
                    security: compromised ? .compromised : .unavailable,
                    secret: resolvedSecret
                )
            }
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
            if cachedSecret == nil {
                // Pin this app's approval key as soon as the daemon is reachable so no
                // other local process can register first. Idempotent for the same key.
                _ = try? client.registerApprovalSigner()
            }
            let providerEvent = IPCProviderStatusEvent(response: response)
            let runtimePowerStateKnown =
                response.payload["runtime_power_state_known"] as? Bool ?? false
            let runtimePower = (try? client.call(method: "runtime.power.status"))
                .flatMap { IPCRuntimePowerStatus(response: $0) }
            let provider = providerEvent
                .map { ProviderReadinessState(rawValue: $0.credential.rawValue) ?? .unavailable }
                ?? .unavailable
            guard let security = IPCSecurityStatusEvent(response: response) else {
                return ProbeResult(
                    state: state,
                    security: .compromised,
                    provider: provider,
                    localBrainAvailable: providerEvent?.localModel == .available,
                    runtimePowerStateKnown: runtimePowerStateKnown,
                    secret: resolvedSecret
                )
            }
            return ProbeResult(
                state: state,
                security: security.integrity == .intact ? .intact : .compromised,
                provider: provider,
                localBrainAvailable: providerEvent?.localModel == .available,
                runtimePowerStateKnown: runtimePowerStateKnown,
                runtimePower: runtimePower,
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
        with transcriber: LocalSpeechTranscriber,
        durationSeconds: TimeInterval
    ) -> CaptureOutcome {
        do {
            guard let transcript = try transcriber.runForFinalTranscript(
                durationSeconds: VoiceCapturePolicy.boundedNormalTurnDuration(durationSeconds),
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
        case let .voice(
            transcript,
            preferredBrowserBundleIdentifier,
            activeApplicationBundleIdentifier
        ):
            try? client.submitVoiceTranscript(
                transcript,
                conversationID: conversationID,
                persistConversation: persistConversation,
                preferredBrowserBundleIdentifier: preferredBrowserBundleIdentifier,
                activeApplicationBundleIdentifier: activeApplicationBundleIdentifier
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

    nonisolated private static func reportTCCPermissionDenied(
        permission: TCCPrivacyPermission,
        operation: TCCPrivacyOperation,
        jobID: UUID?,
        secret: Data
    ) -> Bool {
        guard
            let client = try? LocalIPCClient(secret: secret),
            let response = try? client.reportTCCPermissionDenied(
                permission: permission,
                operation: operation,
                jobID: jobID
            )
        else {
            return false
        }
        return response.ok
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
        let actionStartedAt = ProcessInfo.processInfo.systemUptime
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
        if command["command"] as? String == "capture",
           helperResponse["status"] as? String == "ok",
           let encoded = helperResponse["data_base64"] as? String,
           encoded.utf8.count <= 43_692,
           let image = Data(base64Encoded: encoded),
           (1 ... 32_768).contains(image.count) {
            let captureMilliseconds = max(
                0,
                Int(((ProcessInfo.processInfo.systemUptime - actionStartedAt) * 1_000).rounded())
            )
            Task.detached(priority: .utility) {
                try? await JarvisOperationalEvidenceRecorder.shared.recordScreenCapture(
                    milliseconds: captureMilliseconds,
                    payloadBytes: image.count
                )
            }
        }
        if command["command"] as? String == "act" {
            let verification = helperResponse["action_verification"] as? [String: Any]
            let verified = helperResponse["status"] as? String == "ok"
                && verification?["verified"] as? Bool == true
            let actionMilliseconds = max(
                0,
                Int(
                    ((ProcessInfo.processInfo.systemUptime - actionStartedAt) * 1_000)
                        .rounded()
                )
            )
            Task.detached(priority: .utility) {
                try? await JarvisOperationalEvidenceRecorder.shared.recordComputerAction(
                    verified: verified,
                    milliseconds: actionMilliseconds
                )
            }
        }
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
        always: Bool = false,
        secret: Data
    ) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).approveJob(
                approval.jobID,
                callDigest: approval.confirmation.callDigest,
                always: always
            ),
            let status = IPCJobStatusEvent(response: response),
            status.jobID == approval.jobID,
            status.state == .running
        else {
            return false
        }
        return true
    }

    nonisolated private static func approveJobByVoice(
        _ approval: PendingApproval,
        evidence: VoiceAuthorizationEvidence,
        secret: Data
    ) -> Bool {
        // The packaged daemon cannot run MLX Whisper, so the app sends what on-device
        // Speech heard during the approval window, signed together with the audio.
        let transcript = evidence.transcript
        guard
            let response = try? LocalIPCClient(secret: secret).approveJobByVoice(
                approval.jobID,
                callDigest: approval.confirmation.callDigest,
                pcmS16LE: evidence.pcmS16LE,
                speakerIdentifier: evidence.speakerIdentifier,
                speakerConfidence: evidence.speakerConfidence,
                ownerProfileMatch: evidence.ownerProfileMatch,
                transcript: transcript
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
        let runtimePowerStateKnown: Bool
        let runtimePower: IPCRuntimePowerStatus?
        let secret: Data?

        init(
            state: DaemonConnectionState,
            security: SecurityMonitorState,
            provider: ProviderReadinessState = .unknown,
            localBrainAvailable: Bool = false,
            runtimePowerStateKnown: Bool = false,
            runtimePower: IPCRuntimePowerStatus? = nil,
            secret: Data?
        ) {
            self.state = state
            self.security = security
            self.provider = provider
            self.localBrainAvailable = localBrainAvailable
            self.runtimePowerStateKnown = runtimePowerStateKnown
            self.runtimePower = runtimePower
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
        case voice(
            SpeechTranscriptEvent,
            preferredBrowserBundleIdentifier: String?,
            activeApplicationBundleIdentifier: String?
        )
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
        case cancelled
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
        case voiceActivityUnavailable
        case speakerIdentityUnverified
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
            case .voiceActivityUnavailable:
                self = .voiceActivityUnavailable
            case .speakerIdentityUnverified:
                self = .speakerIdentityUnverified
            case .recognitionFailed:
                self = .recognitionFailed
            case .cancelled:
                self = .cancelled
            }
        }
    }
}
