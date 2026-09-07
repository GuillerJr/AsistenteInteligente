import Foundation
import OSLog

private let readinessLogger = Logger(
    subsystem: "ai.aegis.menubar",
    category: "Reliability"
)

public struct JarvisReadinessSnapshot: Codable, Equatable, Sendable {
    public static let schemaVersion = "1.0"

    public let schemaVersion: String
    public let buildRevision: String
    public let daemon: String
    public let security: String
    public let provider: String
    public let localBrainAvailable: Bool
    public let microphone: String
    public let speechRecognition: String
    public let screenCaptureAuthorized: Bool
    public let computerControl: String
    public let wakeWord: String
    public let wakeWordEnabled: Bool
    public let speakerIdentity: String

    public init(
        daemon: String,
        security: String,
        provider: String,
        localBrainAvailable: Bool,
        microphone: String,
        speechRecognition: String,
        screenCaptureAuthorized: Bool,
        computerControl: String,
        wakeWord: String,
        wakeWordEnabled: Bool,
        speakerIdentity: String,
        buildRevision: String = JarvisBuildIdentity.current()
    ) {
        schemaVersion = Self.schemaVersion
        self.buildRevision = buildRevision
        self.daemon = daemon
        self.security = security
        self.provider = provider
        self.localBrainAvailable = localBrainAvailable
        self.microphone = microphone
        self.speechRecognition = speechRecognition
        self.screenCaptureAuthorized = screenCaptureAuthorized
        self.computerControl = computerControl
        self.wakeWord = wakeWord
        self.wakeWordEnabled = wakeWordEnabled
        self.speakerIdentity = speakerIdentity
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case buildRevision = "build_revision"
        case daemon
        case security
        case provider
        case localBrainAvailable = "local_brain_available"
        case microphone
        case speechRecognition = "speech_recognition"
        case screenCaptureAuthorized = "screen_capture_authorized"
        case computerControl = "computer_control"
        case wakeWord = "wake_word"
        case wakeWordEnabled = "wake_word_enabled"
        case speakerIdentity = "speaker_identity"
    }
}

public enum JarvisReadinessStoreError: Error, Equatable, Sendable {
    case invalidBuildRevision
    case unsafeDestination
}

public enum JarvisReadinessStore {
    public static func defaultURL(
        fileManager: FileManager = .default
    ) throws -> URL {
        guard let applicationSupport = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw JarvisReadinessStoreError.unsafeDestination
        }
        return applicationSupport
            .appendingPathComponent("Aegis", isDirectory: true)
            .appendingPathComponent("runtime-readiness.json", isDirectory: false)
    }

    public static func persist(
        _ snapshot: JarvisReadinessSnapshot,
        to destination: URL? = nil,
        fileManager: FileManager = .default
    ) throws {
        guard JarvisBuildIdentity.isValid(snapshot.buildRevision) else {
            throw JarvisReadinessStoreError.invalidBuildRevision
        }
        let target = try destination ?? defaultURL(fileManager: fileManager)
        let directory = target.deletingLastPathComponent()
        try validateRegularDestination(target, fileManager: fileManager)
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try fileManager.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: directory.path
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        var encoded = try encoder.encode(snapshot)
        encoded.append(0x0A)
        if let existing = try? Data(contentsOf: target), existing == encoded {
            return
        }
        try encoded.write(to: target, options: .atomic)
        try validateRegularDestination(target, fileManager: fileManager)
        try fileManager.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: target.path
        )
        readinessLogger.info(
            "readiness_persisted build=\(JarvisBuildIdentity.short(snapshot.buildRevision), privacy: .public) daemon=\(snapshot.daemon, privacy: .public) security=\(snapshot.security, privacy: .public)"
        )
    }

    private static func validateRegularDestination(
        _ destination: URL,
        fileManager: FileManager
    ) throws {
        guard fileManager.fileExists(atPath: destination.path) else { return }
        let values = try destination.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
        )
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw JarvisReadinessStoreError.unsafeDestination
        }
    }
}
