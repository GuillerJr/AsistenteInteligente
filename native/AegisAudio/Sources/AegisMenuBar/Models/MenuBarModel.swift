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

enum VoiceTurnState: Sendable {
    case idle
    case listening
    case submitting
    case submitted
    case failed

    var title: String {
        switch self {
        case .idle:
            "listo"
        case .listening:
            "escuchando"
        case .submitting:
            "enviando"
        case .submitted:
            "enviado"
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
        case .submitted:
            "checkmark.circle.fill"
        case .failed:
            "exclamationmark.circle.fill"
        }
    }

    var isBusy: Bool {
        switch self {
        case .listening, .submitting:
            true
        case .idle, .submitted, .failed:
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
        let submitted = await Task.detached(priority: .utility) {
            Self.submitTranscript(transcript, secret: secret)
        }.value
        voiceState = submitted ? .submitted : .failed
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
    ) -> Bool {
        guard
            let response = try? LocalIPCClient(secret: secret).submitVoiceTranscript(transcript)
        else {
            return false
        }
        return response.ok && VoiceSubmissionEvent(response: response) != nil
    }

    private struct ProbeResult: Sendable {
        let state: DaemonConnectionState
        let secret: Data?
    }
}
