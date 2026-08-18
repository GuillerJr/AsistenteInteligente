import AegisAudioCore
import Darwin
import Foundation

private enum ExitCode: Int32 {
    case success = 0
    case invalidArguments = 64
    case permissionRequired = 77
    case unavailable = 69
    case invalidConfiguration = 78
}

private struct MeterOptions {
    let durationSeconds: TimeInterval
    let intervalMilliseconds: Int

    init(arguments: ArraySlice<String>) throws {
        var duration: TimeInterval = 10
        var interval = 50
        var index = arguments.startIndex
        while index < arguments.endIndex {
            let option = arguments[index]
            index = arguments.index(after: index)
            guard index < arguments.endIndex else {
                throw CLIError.invalidArguments
            }
            let value = arguments[index]
            index = arguments.index(after: index)
            switch option {
            case "--duration-seconds":
                guard let parsed = TimeInterval(value), (1 ... 60).contains(parsed) else {
                    throw CLIError.invalidArguments
                }
                duration = parsed
            case "--interval-ms":
                guard let parsed = Int(value), (20 ... 250).contains(parsed) else {
                    throw CLIError.invalidArguments
                }
                interval = parsed
            default:
                throw CLIError.invalidArguments
            }
        }
        durationSeconds = duration
        intervalMilliseconds = interval
    }
}

private struct SpeechOptions {
    let durationSeconds: TimeInterval
    let intervalMilliseconds: Int
    let localeIdentifier: String

    init(arguments: ArraySlice<String>, capture: Bool) throws {
        var duration: TimeInterval = 10
        var interval = 50
        var locale = "es-US"
        var index = arguments.startIndex
        while index < arguments.endIndex {
            let option = arguments[index]
            index = arguments.index(after: index)
            guard index < arguments.endIndex else {
                throw CLIError.invalidArguments
            }
            let value = arguments[index]
            index = arguments.index(after: index)
            switch option {
            case "--duration-seconds" where capture:
                guard let parsed = TimeInterval(value), (1 ... 60).contains(parsed) else {
                    throw CLIError.invalidArguments
                }
                duration = parsed
            case "--interval-ms" where capture:
                guard let parsed = Int(value), (20 ... 250).contains(parsed) else {
                    throw CLIError.invalidArguments
                }
                interval = parsed
            case "--locale":
                guard let normalized = SpeechLocale.normalized(value) else {
                    throw CLIError.invalidArguments
                }
                locale = normalized
            default:
                throw CLIError.invalidArguments
            }
        }
        durationSeconds = duration
        intervalMilliseconds = interval
        localeIdentifier = locale
    }
}

private struct IPCOptions {
    let socketPath: String
    let keychainService: String
    let keychainAccount: String

    init(arguments: ArraySlice<String>) throws {
        var socketPath = LocalIPCClient.defaultSocketPath
        var keychainService = "ai.aegis.ipc-auth"
        var keychainAccount = "default"
        var index = arguments.startIndex
        while index < arguments.endIndex {
            let option = arguments[index]
            index = arguments.index(after: index)
            guard index < arguments.endIndex else {
                throw CLIError.invalidArguments
            }
            let value = arguments[index]
            index = arguments.index(after: index)
            switch option {
            case "--socket-path":
                guard value.hasPrefix("/"), !value.contains("\0") else {
                    throw CLIError.invalidArguments
                }
                socketPath = value
            case "--keychain-service":
                keychainService = value
            case "--keychain-account":
                keychainAccount = value
            default:
                throw CLIError.invalidArguments
            }
        }
        self.socketPath = socketPath
        self.keychainService = keychainService
        self.keychainAccount = keychainAccount
    }
}

private struct TranscribeSubmitOptions {
    let speech: SpeechOptions
    let ipc: IPCOptions
    let conversationID: UUID?

    init(arguments: ArraySlice<String>) throws {
        var speechArguments: [String] = []
        var ipcArguments: [String] = []
        var conversationID: UUID?
        var index = arguments.startIndex
        while index < arguments.endIndex {
            let option = arguments[index]
            index = arguments.index(after: index)
            guard index < arguments.endIndex else {
                throw CLIError.invalidArguments
            }
            let value = arguments[index]
            index = arguments.index(after: index)
            switch option {
            case "--duration-seconds", "--interval-ms", "--locale":
                speechArguments.append(contentsOf: [option, value])
            case "--socket-path", "--keychain-service", "--keychain-account":
                ipcArguments.append(contentsOf: [option, value])
            case "--conversation-id":
                guard conversationID == nil, let parsed = UUID(uuidString: value) else {
                    throw CLIError.invalidArguments
                }
                conversationID = parsed
            default:
                throw CLIError.invalidArguments
            }
        }
        speech = try SpeechOptions(arguments: speechArguments[...], capture: true)
        ipc = try IPCOptions(arguments: ipcArguments[...])
        self.conversationID = conversationID
    }
}

private struct SubmitTranscriptOptions {
    let ipc: IPCOptions
    let conversationID: UUID?

    init(arguments: ArraySlice<String>) throws {
        var ipcArguments: [String] = []
        var conversationID: UUID?
        var index = arguments.startIndex
        while index < arguments.endIndex {
            let option = arguments[index]
            index = arguments.index(after: index)
            guard index < arguments.endIndex else {
                throw CLIError.invalidArguments
            }
            let value = arguments[index]
            index = arguments.index(after: index)
            switch option {
            case "--socket-path", "--keychain-service", "--keychain-account":
                ipcArguments.append(contentsOf: [option, value])
            case "--conversation-id":
                guard conversationID == nil, let parsed = UUID(uuidString: value) else {
                    throw CLIError.invalidArguments
                }
                conversationID = parsed
            default:
                throw CLIError.invalidArguments
            }
        }
        ipc = try IPCOptions(arguments: ipcArguments[...])
        self.conversationID = conversationID
    }
}

private enum CLIError: Error {
    case invalidArguments
}

private func emitStatus(
    state: String,
    permission: MicrophonePermission,
    errorCode: String? = nil,
    writer: NDJSONWriter
) {
    try? writer.write(
        AudioHelperStatus(state: state, permission: permission, errorCode: errorCode)
    )
}

private func emitSpeechStatus(
    state: String,
    localeIdentifier: String,
    errorCode: String? = nil,
    transcriptAvailable: Bool? = nil,
    writer: NDJSONWriter
) {
    guard let status = SpeechStatusEvent.inspect(
        state: state,
        localeIdentifier: localeIdentifier,
        transcriptAvailable: transcriptAvailable,
        errorCode: errorCode
    ) else {
        return
    }
    try? writer.write(status)
}

private func speechErrorCode(_ error: LocalSpeechTranscriberError) -> String {
    switch error {
    case .invalidConfiguration:
        "invalid_arguments"
    case .microphonePermissionRequired:
        "microphone_permission_required"
    case .speechPermissionRequired:
        "speech_permission_required"
    case .unsupportedLocale:
        "speech_locale_unsupported"
    case .recognizerUnavailable:
        "speech_recognizer_unavailable"
    case .onDeviceRecognitionUnavailable:
        "on_device_speech_unavailable"
    case .invalidInputFormat:
        "audio_capture_unavailable"
    }
}

private func ipcErrorCode(_ error: LocalIPCError) -> String {
    switch error {
    case .invalidConfiguration:
        "ipc_invalid_configuration"
    case .keychainCredentialUnavailable:
        "ipc_credential_unavailable"
    case .invalidCredential:
        "ipc_credential_invalid"
    case .socketUnavailable:
        "ipc_socket_unavailable"
    case .unsafeSocket:
        "ipc_socket_unsafe"
    case .connectionFailed:
        "ipc_connection_failed"
    case .frameTooLarge:
        "ipc_frame_too_large"
    case .malformedResponse:
        "ipc_response_malformed"
    case .responseMismatch:
        "ipc_response_mismatch"
    case .responseAuthenticationFailed:
        "ipc_response_authentication_failed"
    case .staleResponse:
        "ipc_response_stale"
    }
}

private func makeIPCClient(options: IPCOptions) throws -> LocalIPCClient {
    let secretStore = try MacOSIPCSecretStore(
        service: options.keychainService,
        account: options.keychainAccount
    )
    return try LocalIPCClient(socketPath: options.socketPath, secretStore: secretStore)
}

private func emitIPCFailure(_ errorCode: String, writer: NDJSONWriter) {
    try? writer.write(IPCStatusEvent(state: "failed", errorCode: errorCode))
}

private func readBoundedStandardInput(maxBytes: Int) throws -> Data {
    var result = Data()
    var buffer = [UInt8](repeating: 0, count: 4_096)
    while true {
        let count = buffer.withUnsafeMutableBytes { storage in
            Darwin.read(STDIN_FILENO, storage.baseAddress, storage.count)
        }
        if count > 0 {
            result.append(contentsOf: buffer.prefix(count))
            guard result.count <= maxBytes else {
                throw CLIError.invalidArguments
            }
        } else if count == 0 {
            guard !result.isEmpty else {
                throw CLIError.invalidArguments
            }
            return result
        } else if errno == EINTR {
            continue
        } else {
            throw CLIError.invalidArguments
        }
    }
}

private func main() -> ExitCode {
    let arguments = CommandLine.arguments.dropFirst()
    let writer = NDJSONWriter()
    guard let command = arguments.first else {
        emitStatus(
            state: "failed",
            permission: .current,
            errorCode: "invalid_arguments",
            writer: writer
        )
        return .invalidArguments
    }

    switch command {
    case "permission":
        let permission = MicrophonePermission.current
        emitStatus(state: "permission", permission: permission, writer: writer)
        return permission == .authorized ? .success : .permissionRequired
    case "meter":
        do {
            let options = try MeterOptions(arguments: arguments.dropFirst())
            try MicrophoneMeter(writer: writer).run(
                durationSeconds: options.durationSeconds,
                intervalMilliseconds: options.intervalMilliseconds
            )
            return .success
        } catch CLIError.invalidArguments {
            emitStatus(
                state: "failed",
                permission: .current,
                errorCode: "invalid_arguments",
                writer: writer
            )
            return .invalidArguments
        } catch let MicrophoneMeterError.permissionRequired(permission) {
            emitStatus(
                state: "permission_required",
                permission: permission,
                errorCode: "microphone_permission_required",
                writer: writer
            )
            return .permissionRequired
        } catch {
            emitStatus(
                state: "failed",
                permission: .current,
                errorCode: "audio_capture_unavailable",
                writer: writer
            )
            return .unavailable
        }
    case "speech-status":
        do {
            let options = try SpeechOptions(arguments: arguments.dropFirst(), capture: false)
            guard let status = SpeechStatusEvent.inspect(
                state: "capability",
                localeIdentifier: options.localeIdentifier
            ) else {
                throw CLIError.invalidArguments
            }
            try writer.write(status)
            if status.ready {
                return .success
            }
            if status.microphonePermission != .authorized
                || status.speechPermission != .authorized {
                return .permissionRequired
            }
            return .unavailable
        } catch {
            emitStatus(
                state: "failed",
                permission: .current,
                errorCode: "invalid_arguments",
                writer: writer
            )
            return .invalidArguments
        }
    case "transcribe":
        do {
            let options = try SpeechOptions(arguments: arguments.dropFirst(), capture: true)
            _ = try LocalSpeechTranscriber(writer: writer).run(
                durationSeconds: options.durationSeconds,
                intervalMilliseconds: options.intervalMilliseconds,
                localeIdentifier: options.localeIdentifier
            )
            return .success
        } catch let error as LocalSpeechTranscriberError {
            let options = try? SpeechOptions(arguments: arguments.dropFirst(), capture: true)
            emitSpeechStatus(
                state: "failed",
                localeIdentifier: options?.localeIdentifier ?? "es-US",
                errorCode: speechErrorCode(error),
                writer: writer
            )
            switch error {
            case .invalidConfiguration:
                return .invalidArguments
            case .microphonePermissionRequired, .speechPermissionRequired:
                return .permissionRequired
            default:
                return .unavailable
            }
        } catch CLIError.invalidArguments {
            emitStatus(
                state: "failed",
                permission: .current,
                errorCode: "invalid_arguments",
                writer: writer
            )
            return .invalidArguments
        } catch {
            emitSpeechStatus(
                state: "failed",
                localeIdentifier: "es-US",
                errorCode: "speech_recognition_failed",
                writer: writer
            )
            return .unavailable
        }
    case "ipc-health":
        do {
            let options = try IPCOptions(arguments: arguments.dropFirst())
            let response = try makeIPCClient(options: options).health()
            guard response.ok, let event = IPCHealthEvent(response: response) else {
                emitIPCFailure(response.errorCode ?? "ipc_daemon_rejected", writer: writer)
                return .unavailable
            }
            try writer.write(event)
            return .success
        } catch CLIError.invalidArguments {
            emitIPCFailure("invalid_arguments", writer: writer)
            return .invalidArguments
        } catch let error as LocalIPCError {
            emitIPCFailure(ipcErrorCode(error), writer: writer)
            switch error {
            case .invalidConfiguration, .keychainCredentialUnavailable, .invalidCredential:
                return .invalidConfiguration
            default:
                return .unavailable
            }
        } catch {
            emitIPCFailure("ipc_unavailable", writer: writer)
            return .unavailable
        }
    case "transcribe-submit":
        do {
            let options = try TranscribeSubmitOptions(arguments: arguments.dropFirst())
            let client = try makeIPCClient(options: options.ipc)
            let health = try client.health()
            guard health.ok, IPCHealthEvent(response: health) != nil else {
                emitIPCFailure(health.errorCode ?? "ipc_daemon_rejected", writer: writer)
                return .unavailable
            }
            guard let transcript = try LocalSpeechTranscriber(writer: writer)
                .runForFinalTranscript(
                    durationSeconds: options.speech.durationSeconds,
                    intervalMilliseconds: options.speech.intervalMilliseconds,
                    localeIdentifier: options.speech.localeIdentifier
                )
            else {
                emitIPCFailure("speech_final_transcript_unavailable", writer: writer)
                return .unavailable
            }
            let response = try client.submitVoiceTranscript(
                transcript,
                conversationID: options.conversationID
            )
            guard response.ok, let event = VoiceSubmissionEvent(response: response) else {
                emitIPCFailure(response.errorCode ?? "ipc_daemon_rejected", writer: writer)
                return .unavailable
            }
            try writer.write(event)
            return .success
        } catch CLIError.invalidArguments {
            emitIPCFailure("invalid_arguments", writer: writer)
            return .invalidArguments
        } catch let error as LocalSpeechTranscriberError {
            let options = try? TranscribeSubmitOptions(arguments: arguments.dropFirst())
            emitSpeechStatus(
                state: "failed",
                localeIdentifier: options?.speech.localeIdentifier ?? "es-US",
                errorCode: speechErrorCode(error),
                writer: writer
            )
            switch error {
            case .invalidConfiguration:
                return .invalidArguments
            case .microphonePermissionRequired, .speechPermissionRequired:
                return .permissionRequired
            default:
                return .unavailable
            }
        } catch let error as LocalIPCError {
            emitIPCFailure(ipcErrorCode(error), writer: writer)
            switch error {
            case .invalidConfiguration, .keychainCredentialUnavailable, .invalidCredential:
                return .invalidConfiguration
            default:
                return .unavailable
            }
        } catch {
            emitIPCFailure("ipc_unavailable", writer: writer)
            return .unavailable
        }
    case "submit-transcript":
        do {
            let options = try SubmitTranscriptOptions(arguments: arguments.dropFirst())
            let transcriptData = try readBoundedStandardInput(
                maxBytes: SpeechTranscriptEvent.maximumJSONBytes
            )
            let transcript = try SpeechTranscriptEvent.decodeStrictJSON(transcriptData)
            guard transcript.isFinal, transcript.onDevice else {
                emitIPCFailure("invalid_transcript", writer: writer)
                return .invalidArguments
            }
            let response = try makeIPCClient(options: options.ipc).submitVoiceTranscript(
                transcript,
                conversationID: options.conversationID
            )
            guard response.ok, let event = VoiceSubmissionEvent(response: response) else {
                emitIPCFailure(response.errorCode ?? "ipc_daemon_rejected", writer: writer)
                return .unavailable
            }
            try writer.write(event)
            return .success
        } catch CLIError.invalidArguments {
            emitIPCFailure("invalid_arguments", writer: writer)
            return .invalidArguments
        } catch is DecodingError {
            emitIPCFailure("invalid_transcript", writer: writer)
            return .invalidArguments
        } catch let error as LocalIPCError {
            emitIPCFailure(ipcErrorCode(error), writer: writer)
            switch error {
            case .invalidConfiguration, .keychainCredentialUnavailable, .invalidCredential:
                return .invalidConfiguration
            default:
                return .unavailable
            }
        } catch {
            emitIPCFailure("ipc_unavailable", writer: writer)
            return .unavailable
        }
    default:
        emitStatus(
            state: "failed",
            permission: .current,
            errorCode: "invalid_arguments",
            writer: writer
        )
        return .invalidArguments
    }
}

exit(main().rawValue)
