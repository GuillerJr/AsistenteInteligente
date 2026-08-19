import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
@preconcurrency import Speech

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

enum VoiceTurnState: Equatable, Sendable {
    case idle
    case listening
    case submitting
    case processing
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
        case .idle, .speaking, .completed, .failed:
            false
        }
    }
}

@MainActor
@Observable
final class MenuBarModel {
    var daemonState = DaemonConnectionState.unknown
    var voiceState = VoiceTurnState.idle
    var microphonePermission = MicrophonePermission.current
    var speechPermission = SpeechRecognitionPermission.current
    @ObservationIgnored private var ipcSecret: Data?
    @ObservationIgnored private var monitoring = false
    @ObservationIgnored private let speechOutput = SpeechOutput()

    var canStartVoiceTurn: Bool {
        daemonState == .online
            && microphonePermission == .authorized
            && speechPermission == .authorized
            && !voiceState.isBusy
    }

    var menuBarSymbol: String {
        voiceState.isBusy ? voiceState.symbol : daemonState.symbol
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
        daemonState = .checking
        let result = await Task.detached(priority: .utility) { [ipcSecret] in
            Self.probeDaemon(cachedSecret: ipcSecret)
        }.value
        daemonState = result.state
        ipcSecret = result.secret
    }

    func requestMicrophone() async {
        guard MicrophonePermission.current == .notDetermined else {
            openPrivacySettings("Privacy_Microphone")
            return
        }
        _ = await AVCaptureDevice.requestAccess(for: .audio)
        refreshPermissions()
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

    func startVoiceTurn() async {
        guard !voiceState.isBusy else {
            return
        }
        voiceState = .idle
        speechOutput.stop()
        refreshPermissions()
        await refreshDaemon()
        guard
            canStartVoiceTurn,
            let secret = ipcSecret
        else {
            voiceState = .failed
            return
        }

        voiceState = .listening
        let transcript = await Task.detached(priority: .userInitiated) {
            Self.captureTranscript()
        }.value
        guard let transcript else {
            voiceState = .failed
            return
        }

        voiceState = .submitting
        let jobID = await Task.detached(priority: .utility) {
            Self.submitTranscript(transcript, secret: secret)
        }.value
        guard let jobID else {
            voiceState = .failed
            return
        }

        voiceState = .processing
        let outcome = await Self.waitForJob(jobID, secret: secret)
        guard case let .completed(result) = outcome else {
            voiceState = .failed
            return
        }
        let spokenText = String(result.split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ").prefix(2_000))
        guard !spokenText.isEmpty else {
            voiceState = .failed
            return
        }
        voiceState = .speaking
        speechOutput.speak(spokenText) { [weak self] in
            guard self?.voiceState == .speaking else { return }
            self?.voiceState = .completed
        }
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
                return ProbeResult(state: .offline, secret: nil)
            }
            let response = try LocalIPCClient(secret: resolvedSecret).health()
            let state: DaemonConnectionState =
                response.ok && response.payload["status"] as? String == "ok"
                ? .online : .offline
            return ProbeResult(state: state, secret: resolvedSecret)
        } catch let error as LocalIPCError {
            switch error {
            case .invalidCredential, .responseMismatch, .responseAuthenticationFailed:
                return ProbeResult(state: .securityFailure, secret: nil)
            case .unsafeSocket, .staleResponse, .malformedResponse:
                return ProbeResult(state: .securityFailure, secret: resolvedSecret)
            default:
                return ProbeResult(state: .offline, secret: resolvedSecret)
            }
        } catch {
            return ProbeResult(state: .offline, secret: resolvedSecret)
        }
    }

    nonisolated private static func captureTranscript() -> SpeechTranscriptEvent? {
        try? LocalSpeechTranscriber(writer: NDJSONWriter(handle: .nullDevice))
            .runForFinalTranscript(
                durationSeconds: 8,
                intervalMilliseconds: 50,
                localeIdentifier: "es-US"
            )
    }

    nonisolated private static func submitTranscript(
        _ transcript: SpeechTranscriptEvent,
        secret: Data
    ) -> UUID? {
        guard
            let response = try? LocalIPCClient(secret: secret).submitVoiceTranscript(transcript),
            let submission = VoiceSubmissionEvent(response: response)
        else {
            return nil
        }
        return submission.jobID
    }

    nonisolated private static func waitForJob(_ jobID: UUID, secret: Data) async -> JobOutcome {
        for attempt in 0 ..< 120 {
            guard let status = fetchJob(jobID, secret: secret), status.jobID == jobID else {
                return .failed
            }
            switch status.state {
            case .completed:
                guard let result = status.result else { return .failed }
                return .completed(result)
            case .failed, .cancelled:
                return .failed
            case .queued, .running:
                if attempt < 119 {
                    try? await Task.sleep(for: .milliseconds(500))
                }
            }
        }
        return .failed
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

    private struct ProbeResult: Sendable {
        let state: DaemonConnectionState
        let secret: Data?
    }

    private enum JobOutcome: Sendable {
        case completed(String)
        case failed
    }
}
