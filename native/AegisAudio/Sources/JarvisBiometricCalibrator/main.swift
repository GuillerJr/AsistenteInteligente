import AegisAudioCore
import Darwin
import Foundation

private enum Exit: Int32 {
    case accepted = 0
    case rejected = 2
    case invalidConfiguration = 64
    case unavailable = 69
}

private func finish(_ status: Exit, message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(status.rawValue)
}

let arguments = CommandLine.arguments
guard
    (2 ... 3).contains(arguments.count),
    SpeakerIdentityCapability.isValidSpeakerLabel(arguments[1])
else {
    finish(.invalidConfiguration, message: "status=error reason=invalid_configuration")
}
let modelURL = arguments.count == 3
    ? URL(fileURLWithPath: arguments[2], isDirectory: true).standardizedFileURL
    : SpeakerIdentityCapability.modelURL()
guard
    let modelURL,
    SpeakerIdentityCapability.inspect(modelURL: modelURL) == .ready
else {
    finish(.invalidConfiguration, message: "status=error reason=model_invalid")
}

let report = SpeakerAdversarialCalibrator(
    modelURL: modelURL,
    distractorDirectory: DualChannelAuthorizer.defaultDistractorDirectory,
    enrollmentDirectory: DualChannelAuthorizer.defaultEnrollmentDirectory
).analyze(
    ownerIdentifier: arguments[1],
    expectedModelFingerprint: nil
)
guard let report else {
    finish(.unavailable, message: "status=error reason=calibration_unavailable")
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
guard let encoded = try? encoder.encode(report) else {
    finish(.unavailable, message: "status=error reason=encoding_failed")
}
FileHandle.standardOutput.write(encoded)
FileHandle.standardOutput.write(Data("\n".utf8))
exit(report.accepted ? Exit.accepted.rawValue : Exit.rejected.rawValue)
