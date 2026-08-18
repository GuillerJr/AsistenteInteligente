import AegisAudioCore
import Darwin
import Foundation

private enum ExitCode: Int32 {
    case success = 0
    case invalidArguments = 64
    case permissionRequired = 77
    case unavailable = 69
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
