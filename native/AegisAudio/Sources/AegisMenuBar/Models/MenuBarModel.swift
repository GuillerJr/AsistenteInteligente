import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech

private let voiceConversationDefaultsKey = "ai.aegis.voice.conversation-id"

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
    case recording(WakeWordEnrollmentLabel)
    case ready
    case failed

    var isBusy: Bool {
        switch self {
        case .loading, .recording:
            true
        case .idle, .ready, .failed:
            false
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
    var voiceState = VoiceTurnState.idle
    var microphonePermission = MicrophonePermission.current
    var speechPermission = SpeechRecognitionPermission.current
    var screenCaptureAuthorized = ScreenCaptureService.isAuthorized
    var hudActivity: [IPCSwarmAgentRole: Int] = [:]
    var voiceActivityLevel: Float = 0
    var voiceShortcutAvailable = false
    var wakeWordCapability = WakeWordCapabilityState.missing
    var wakeWordEnrollmentProgress = WakeWordEnrollmentProgress(
        jarvisCount: 0,
        backgroundCount: 0
    )
    var wakeWordEnrollmentState = WakeWordEnrollmentState.idle
    var pendingApproval: PendingApproval?
    var approvalActionInProgress = false
    @ObservationIgnored private var ipcSecret: Data?
    @ObservationIgnored private var conversationID = UserDefaults.standard
        .string(forKey: voiceConversationDefaultsKey)
        .flatMap(UUID.init(uuidString:))
    @ObservationIgnored private var monitoring = false
    @ObservationIgnored private var hudMonitoring = false
    @ObservationIgnored private let logger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "VoiceTurn"
    )
    @ObservationIgnored private let securityLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "SecurityMonitor"
    )
    @ObservationIgnored private let hudLogger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "HUD"
    )
    @ObservationIgnored private let speechOutput = SpeechOutput()

    var canStartVoiceTurn: Bool {
        daemonState == .online
            && securityState == .intact
            && microphonePermission == .authorized
            && speechPermission == .authorized
            && !voiceState.isBusy
            && pendingApproval == nil
    }

    var canStartScreenTurn: Bool {
        canStartVoiceTurn && screenCaptureAuthorized
    }

    var canRecordWakeWordSample: Bool {
        microphonePermission == .authorized
            && !voiceState.isBusy
            && !wakeWordEnrollmentState.isBusy
    }

    func monitor() async {
        guard !monitoring else {
            return
        }
        monitoring = true
        defer { monitoring = false }
        while !Task.isCancelled {
            refreshPermissions()
            await refreshDaemon()
            do {
                try await Task.sleep(for: .seconds(10))
            } catch {
                return
            }
        }
    }

    func refreshDaemon() async {
        let previousSecurityState = securityState
        daemonState = .checking
        securityState = .checking
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
        ipcSecret = result.secret
    }

    func monitorHUDActivity() async {
        guard !hudMonitoring else {
            return
        }
        hudMonitoring = true
        hudLogger.info("hud_opened")
        defer {
            hudActivity = [:]
            hudMonitoring = false
            hudLogger.info("hud_closed")
        }
        while !Task.isCancelled {
            if
                daemonState == .online,
                securityState == .intact,
                let ipcSecret
            {
                hudActivity = await Task.detached(priority: .utility) {
                    Self.fetchHUDActivity(secret: ipcSecret)
                }.value
            } else {
                hudActivity = [:]
            }
            do {
                try await Task.sleep(for: .milliseconds(250))
            } catch {
                return
            }
        }
    }

    func requestMicrophone() async {
        guard MicrophonePermission.current == .notDetermined else {
            openPrivacySettings("Privacy_Microphone")
            return
        }
        _ = await AVCaptureDevice.requestAccess(for: .audio)
        refreshPermissions()
    }

    func inspectWakeWordCapability() async {
        wakeWordCapability = await Task.detached(priority: .utility) {
            WakeWordCapability.inspect()
        }.value
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
            wakeWordEnrollmentState = .failed
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
        wakeWordEnrollmentState = .recording(label)
        let progress = await Task.detached(priority: .userInitiated) {
            try? WakeWordEnrollmentRecorder().record(label: label)
        }.value
        guard let progress else {
            wakeWordEnrollmentState = .failed
            return
        }
        wakeWordEnrollmentProgress = progress
        wakeWordEnrollmentState = progress.isReady ? .ready : .idle
    }

    func requestSpeechRecognition() async {
        guard SpeechRecognitionPermission.current == .notDetermined else {
            openPrivacySettings("Privacy_SpeechRecognition")
            return
        }
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { _ in
                continuation.resume()
            }
        }
        refreshPermissions()
    }

    func requestScreenCapture() {
        _ = ScreenCaptureService.requestAccess()
        screenCaptureAuthorized = ScreenCaptureService.isAuthorized
        if !screenCaptureAuthorized {
            openPrivacySettings("Privacy_ScreenCapture")
        }
    }

    func requestUndeterminedPermissions() async {
        if MicrophonePermission.current == .notDetermined {
            await requestMicrophone()
        }
        if SpeechRecognitionPermission.current == .notDetermined {
            await requestSpeechRecognition()
        }
    }

    func startVoiceTurn() async {
        guard !voiceState.isBusy else {
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
        voiceState = .idle
        speechOutput.speak("Acción denegada.") {}
    }

    private func handleJobOutcome(_ outcome: JobOutcome, jobID: UUID) {
        switch outcome {
        case let .completed(result):
            speakCompletedResult(result)
        case let .awaitingConfirmation(confirmation):
            pendingApproval = PendingApproval(jobID: jobID, confirmation: confirmation)
            voiceState = .awaitingApproval
            logger.info(
                "tool_confirmation_requested tool=\(confirmation.toolName, privacy: .public)"
            )
            speechOutput.speak("Se requiere tu aprobación en Jarvis.") {}
        case let .failed(errorCode):
            pendingApproval = nil
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
        speechOutput.speak(spokenText) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
            self?.logger.info("voice_turn_completed")
        }
    }

    private func captureSpokenPrompt() async -> CaptureOutcome {
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

    private func trackSubmission(_ submission: SubmissionOutcome, secret: Data) async {
        conversationID = submission.conversationID
        UserDefaults.standard.set(
            submission.conversationID.uuidString.lowercased(),
            forKey: voiceConversationDefaultsKey
        )
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

    private func openPrivacySettings(_ pane: String) {
        guard let url = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?\(pane)"
        ) else {
            return
        }
        NSWorkspace.shared.open(url)
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
            let securityResponse = try client.securityStatus()
            guard let security = IPCSecurityStatusEvent(response: securityResponse) else {
                return ProbeResult(
                    state: state,
                    security: .compromised,
                    secret: resolvedSecret
                )
            }
            return ProbeResult(
                state: state,
                security: security.integrity == .intact ? .intact : .compromised,
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
        for attempt in 0 ..< 120 {
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
                if attempt < 119 {
                    try? await Task.sleep(for: .milliseconds(500))
                }
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

    nonisolated private static func fetchHUDActivity(
        secret: Data
    ) -> [IPCSwarmAgentRole: Int] {
        guard
            let response = try? LocalIPCClient(secret: secret).swarmActivity(),
            let event = IPCSwarmActivityEvent(response: response)
        else {
            return [:]
        }
        return Dictionary(
            uniqueKeysWithValues: event.agents.map { ($0.role, $0.activeJobs) }
        )
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
        let secret: Data?
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

    private enum CaptureFailure: String, Sendable {
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
