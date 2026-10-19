#if DEBUG
import AegisAudioCore
import Foundation

enum NotchPreviewMode: String, CaseIterable {
    case idle
    case ambient
    case listening
    case processing
    case approval
    case speaking
    case failure
    case offline
    case checking
    case security
    case uncertain
    case interrupted
    case completed
    case browser
    case input
    case recovery
    case cycle

    init?(arguments: [String]) {
        guard
            let argument = arguments.first(where: { $0.hasPrefix("--notch-preview=") }),
            let value = argument.split(separator: "=", maxSplits: 1).last
        else {
            return nil
        }
        self.init(rawValue: String(value))
    }
}

@MainActor
extension MenuBarModel {
    func applyApprovalPreview() {
        guard let confirmation = IPCPendingConfirmation.debugPreview(
            toolName: "mail_send_message",
            summary: "Enviar correo a ejemplo@example.com; asunto: Reunión del proyecto",
            expiresAt: Date().addingTimeInterval(120),
            previewFields: ["Destinatarios": "ejemplo@example.com", "Asunto": "Reunión del proyecto",
                            "Contenido": "Hola, confirmo la reunión de mañana a las diez.\nRevisaremos los siguientes pasos del proyecto.\nSaludos."]
        ) else {
            return
        }
        pendingApproval = PendingApproval(
            jobID: UUID(),
            confirmation: confirmation
        )
    }

    func applyNotchPreview(_ mode: NotchPreviewMode) {
        // Deterministic display-only capabilities; no system permission requests or defaults writes.
        microphonePermission = .authorized
        speechPermission = .authorized
        screenCaptureAuthorized = true
        computerControlCapability = .ready
        wakeWordCapability = .ready
        speakerIdentityCapability = .ready
        voiceShortcutAvailable = true
        wakeWordOptedIn = mode == .ambient
        proactiveAlertsEnabled = false
        lastSpeakerID = nil
        activeComputerUseJobID = nil
        daemonState = .online
        securityState = .intact
        providerState = .configured
        localBrainAvailable = false
        pendingApproval = nil
        pendingBrowserSelection = nil
        pendingInput = nil
        pendingRecovery = nil
        lastVoiceFailureCode = nil
        isInterruptingSpeech = false
        wakeWordListeningState = mode == .ambient ? .listening : .off
        voiceActivityLevel = mode == .listening ? 0.74 : 0
        hudActivity = [:]

        switch mode {
        case .idle, .ambient, .cycle:
            voiceState = .idle
        case .listening:
            voiceState = .listening
        case .processing:
            voiceState = .processing
            hudActivity = [.planner: 1, .criticalReasoner: 1]
        case .approval:
            voiceState = .awaitingAuthorization
            applyApprovalPreview()
        case .speaking:
            voiceState = .speaking
        case .failure:
            voiceState = .failed
            lastVoiceFailureCode = "remote_provider_unavailable"
        case .offline:
            daemonState = .offline
            voiceState = .completed
        case .checking:
            securityState = .checking
            voiceState = .idle
        case .security:
            securityState = .compromised
            voiceState = .completed
            hudActivity = [.planner: 1]
        case .uncertain:
            voiceState = .speaking
            lastVoiceFailureCode = "job_status_unavailable"
        case .interrupted:
            voiceState = .speaking
            isInterruptingSpeech = true
        case .completed:
            voiceState = .completed
        case .browser:
            voiceState = .idle
            if let transcript = SpeechTranscriptEvent(
                captureID: UUID(), sequence: 0, text: "Solicitud ficticia de prueba",
                localeIdentifier: "es-ES", durationMilliseconds: 1000,
                isFinal: true, confidence: 1
            ) {
                pendingBrowserSelection = PendingBrowserSelection(
                    transcript: transcript, options: IPCBrowserOption.previewOptions)
            }
        case .input:
            voiceState = .completed
            if let request = IPCInputRequest.debugPreview(question: "¿A qué hora será la reunión?",
                                                         requestText: "Agenda una reunión mañana") {
                pendingInput = PendingInput(id: UUID(), request: request)
            }
        case .recovery:
            voiceState = .failed
            pendingRecovery = PendingRecovery(code: "job_status_unavailable",
                                              message: "No pude verificar el estado final de la tarea.")
        }
    }

    func runNotchPreviewCycle() async {
        let modes: [NotchPreviewMode] = [
            .idle, .ambient, .listening, .processing, .approval, .speaking,
            .failure, .offline, .checking, .security, .uncertain, .interrupted, .completed,
        ]
        while !Task.isCancelled {
            for mode in modes {
                applyNotchPreview(mode)
                do {
                    try await Task.sleep(for: .seconds(1.4))
                } catch {
                    return
                }
            }
        }
    }
}
#endif
