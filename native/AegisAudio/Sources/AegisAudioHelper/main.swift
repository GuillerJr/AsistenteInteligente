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
