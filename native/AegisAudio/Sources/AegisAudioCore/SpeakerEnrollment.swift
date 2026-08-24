import Foundation

public enum SpeakerEnrollmentTarget: Hashable, Sendable {
    case background
    case speaker(String)

    public var identifier: String {
        switch self {
        case .background: SpeakerIdentityCapability.backgroundLabel
        case let .speaker(identifier): identifier
        }
    }

    var requiresAudibleVoice: Bool {
        if case .speaker = self { return true }
        return false
    }
}

public struct SpeakerEnrollmentProfile: Equatable, Identifiable, Sendable {
    public let identifier: String
    public let sampleCount: Int

    public var id: String { identifier }

    public init(identifier: String, sampleCount: Int) {
        self.identifier = identifier
        self.sampleCount = max(sampleCount, 0)
    }
}

public struct SpeakerEnrollmentProgress: Equatable, Sendable {
    public static let targetPerLabel = 20

    public let backgroundCount: Int
    public let profiles: [SpeakerEnrollmentProfile]

    public var isReady: Bool {
        (SpeakerIdentityCapability.minimumSpeakerCount
            ... SpeakerIdentityCapability.maximumSpeakerCount).contains(profiles.count)
            && backgroundCount >= Self.targetPerLabel
            && profiles.allSatisfy { $0.sampleCount >= Self.targetPerLabel }
    }

    public init(backgroundCount: Int, profiles: [SpeakerEnrollmentProfile]) {
        self.backgroundCount = max(backgroundCount, 0)
        self.profiles = profiles.sorted { $0.identifier < $1.identifier }
    }

    public func count(for target: SpeakerEnrollmentTarget) -> Int {
        switch target {
        case .background:
            backgroundCount
        case let .speaker(identifier):
            profiles.first { $0.identifier == identifier }?.sampleCount ?? 0
        }
    }
}

public enum SpeakerEnrollmentGuidance {
    public static func instruction(
        target: SpeakerEnrollmentTarget,
        acceptedCount: Int
    ) -> String {
        let count = max(acceptedCount, 0)
        return switch (target, count) {
        case (.speaker, 0 ..< 5):
            "Habla con voz normal y una frase natural"
        case (.speaker, 5 ..< 10):
            "Cambia ligeramente el ritmo y la frase"
        case (.speaker, 10 ..< 15):
            "Cambia la distancia o gira un poco la cabeza"
        case (.speaker, 15 ..< 20):
            "Habla con el ruido cotidiano presente"
        case (.speaker, _):
            "Añade otra variación natural"
        case (.background, 0 ..< 5):
            "Captura silencio real de la habitación"
        case (.background, 5 ..< 10):
            "Captura sonidos cotidianos sin hablar"
        case (.background, 10 ..< 15):
            "Captura una voz que no esté enrolada"
        case (.background, 15 ..< 20):
            "Captura conversación o audio ambiental"
        case (.background, _):
            "Añade otro sonido habitual distinto"
        }
    }
}

public enum SpeakerEnrollmentError: Error, Equatable, Sendable {
    case permissionRequired(MicrophonePermission)
    case unsafeStorage
    case invalidIdentifier
    case duplicateProfile
    case missingProfile
    case capacityReached
    case invalidConfiguration
    case invalidInputFormat
    case sampleTooQuiet
    case sampleClipped
    case recordingFailed
}

struct SpeakerEnrollmentStore: Sendable {
    static let maximumSamplesPerLabel = 500
    static let maximumSampleBytes = 5 * 1_024 * 1_024

    let rootURL: URL

    init(rootURL: URL = Self.defaultRootURL) {
        self.rootURL = rootURL.standardizedFileURL
    }

    static var defaultRootURL: URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return base.appending(path: "Aegis/SpeakerEnrollment", directoryHint: .isDirectory)
    }

    func prepare() throws {
        let manager = FileManager.default
        try prepareDirectory(rootURL, manager: manager)
        do {
            try EnrollmentPendingFiles.removeAbandoned(in: rootURL)
        } catch {
            throw SpeakerEnrollmentError.unsafeStorage
        }
        try prepareDirectory(targetURL(.background), manager: manager)
        _ = try profileIdentifiers(manager: manager)
    }

    func progress() throws -> SpeakerEnrollmentProgress {
        try prepare()
        let identifiers = try profileIdentifiers(manager: .default)
        let profiles = try identifiers.map {
            SpeakerEnrollmentProfile(
                identifier: $0,
                sampleCount: try count(.speaker($0))
            )
        }
        return SpeakerEnrollmentProgress(
            backgroundCount: try count(.background),
            profiles: profiles
        )
    }

    func addProfile(_ identifier: String) throws -> SpeakerEnrollmentProgress {
        guard SpeakerIdentityCapability.isValidSpeakerLabel(identifier) else {
            throw SpeakerEnrollmentError.invalidIdentifier
        }
        try prepare()
        let profiles = try profileIdentifiers(manager: .default)
        guard !profiles.contains(identifier) else {
            throw SpeakerEnrollmentError.duplicateProfile
        }
        guard profiles.count < SpeakerIdentityCapability.maximumSpeakerCount else {
            throw SpeakerEnrollmentError.capacityReached
        }
        try prepareDirectory(targetURL(.speaker(identifier)), manager: .default)
        return try progress()
    }

    func removeProfile(_ identifier: String) throws -> SpeakerEnrollmentProgress {
        guard SpeakerIdentityCapability.isValidSpeakerLabel(identifier) else {
            throw SpeakerEnrollmentError.invalidIdentifier
        }
        try prepare()
        guard try profileIdentifiers(manager: .default).contains(identifier) else {
            throw SpeakerEnrollmentError.missingProfile
        }
        _ = try validatedFiles(.speaker(identifier))
        try FileManager.default.removeItem(at: targetURL(.speaker(identifier)))
        return try progress()
    }

    func clearSamples() throws -> SpeakerEnrollmentProgress {
        try prepare()
        let targets = [SpeakerEnrollmentTarget.background]
            + (try profileIdentifiers(manager: .default)).map(SpeakerEnrollmentTarget.speaker)
        let files = try targets.flatMap(validatedFiles)
        for file in files {
            try FileManager.default.removeItem(at: file)
        }
        return try progress()
    }

    func validate(_ target: SpeakerEnrollmentTarget) throws {
        try prepare()
        try validateTarget(target)
    }

    func makeTemporaryURL() -> URL {
        EnrollmentPendingFiles.makeURL(in: rootURL)
    }

    func commit(_ temporaryURL: URL, target: SpeakerEnrollmentTarget) throws {
        try validateTarget(target)
        guard temporaryURL.deletingLastPathComponent().standardizedFileURL == rootURL else {
            throw SpeakerEnrollmentError.unsafeStorage
        }
        let manager = FileManager.default
        let values = try temporaryURL.resourceValues(forKeys: [
            .fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey,
        ])
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true,
            let size = values.fileSize,
            (1 ... Self.maximumSampleBytes).contains(size),
            try count(target) < Self.maximumSamplesPerLabel
        else {
            throw SpeakerEnrollmentError.recordingFailed
        }
        let destination = targetURL(target).appending(path: "\(UUID().uuidString).caf")
        guard !manager.fileExists(atPath: destination.path) else {
            throw SpeakerEnrollmentError.unsafeStorage
        }
        try manager.moveItem(at: temporaryURL, to: destination)
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
    }

    private func count(_ target: SpeakerEnrollmentTarget) throws -> Int {
        try validatedFiles(target).count
    }

    private func validatedFiles(_ target: SpeakerEnrollmentTarget) throws -> [URL] {
        try validateTarget(target)
        let directory = targetURL(target)
        try validateDirectory(directory)
        let files = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [
                .fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey,
            ],
            options: [.skipsHiddenFiles]
        )
        guard files.count <= Self.maximumSamplesPerLabel else {
            throw SpeakerEnrollmentError.capacityReached
        }
        for file in files {
            let values = try file.resourceValues(forKeys: [
                .fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey,
            ])
            let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
            let permissions = attributes[.posixPermissions] as? Int
            guard
                file.pathExtension.lowercased() == "caf",
                values.isRegularFile == true,
                values.isSymbolicLink != true,
                let size = values.fileSize,
                (1 ... Self.maximumSampleBytes).contains(size),
                let permissions,
                permissions & 0o077 == 0
            else {
                throw SpeakerEnrollmentError.unsafeStorage
            }
        }
        return files
    }

    private func profileIdentifiers(manager: FileManager) throws -> [String] {
        let entries = try manager.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )
        var profiles: [String] = []
        for entry in entries {
            let identifier = entry.lastPathComponent
            try validateDirectory(entry)
            if identifier == SpeakerIdentityCapability.backgroundLabel {
                continue
            }
            guard SpeakerIdentityCapability.isValidSpeakerLabel(identifier) else {
                throw SpeakerEnrollmentError.unsafeStorage
            }
            profiles.append(identifier)
        }
        guard profiles.count <= SpeakerIdentityCapability.maximumSpeakerCount else {
            throw SpeakerEnrollmentError.capacityReached
        }
        return profiles.sorted()
    }

    private func validateTarget(_ target: SpeakerEnrollmentTarget) throws {
        if case let .speaker(identifier) = target {
            guard SpeakerIdentityCapability.isValidSpeakerLabel(identifier) else {
                throw SpeakerEnrollmentError.invalidIdentifier
            }
            guard try profileIdentifiers(manager: .default).contains(identifier) else {
                throw SpeakerEnrollmentError.missingProfile
            }
        }
    }

    private func targetURL(_ target: SpeakerEnrollmentTarget) -> URL {
        rootURL.appending(path: target.identifier, directoryHint: .isDirectory)
    }

    private func prepareDirectory(_ url: URL, manager: FileManager) throws {
        if !manager.fileExists(atPath: url.path) {
            try manager.createDirectory(
                at: url,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
        }
        try validateDirectory(url)
    }

    private func validateDirectory(_ url: URL) throws {
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        let permissions = attributes[.posixPermissions] as? Int
        guard
            values.isDirectory == true,
            values.isSymbolicLink != true,
            let permissions,
            permissions & 0o077 == 0
        else {
            throw SpeakerEnrollmentError.unsafeStorage
        }
    }
}

public final class SpeakerEnrollmentRecorder {
    private let store: SpeakerEnrollmentStore

    public init() {
        store = SpeakerEnrollmentStore()
    }

    init(store: SpeakerEnrollmentStore) {
        self.store = store
    }

    public func progress() throws -> SpeakerEnrollmentProgress {
        try store.progress()
    }

    public func addProfile(_ identifier: String) throws -> SpeakerEnrollmentProgress {
        try store.addProfile(identifier)
    }

    public func removeProfile(_ identifier: String) throws -> SpeakerEnrollmentProgress {
        try store.removeProfile(identifier)
    }

    public func clearSamples() throws -> SpeakerEnrollmentProgress {
        try store.clearSamples()
    }

    public func record(
        target: SpeakerEnrollmentTarget,
        durationSeconds: TimeInterval = 3
    ) throws -> SpeakerEnrollmentProgress {
        guard durationSeconds.isFinite, (2 ... 6).contains(durationSeconds) else {
            throw SpeakerEnrollmentError.invalidConfiguration
        }
        try store.validate(target)
        let temporaryURL = store.makeTemporaryURL()
        defer { try? FileManager.default.removeItem(at: temporaryURL) }
        do {
            try EnrollmentAudioCapture.record(
                to: temporaryURL,
                durationSeconds: durationSeconds,
                requiresAudibleVoice: target.requiresAudibleVoice
            )
        } catch let error as EnrollmentAudioCaptureError {
            throw SpeakerEnrollmentError(error)
        }
        try store.commit(temporaryURL, target: target)
        return try store.progress()
    }
}

private extension SpeakerEnrollmentError {
    init(_ error: EnrollmentAudioCaptureError) {
        self = switch error {
        case let .permissionRequired(permission): .permissionRequired(permission)
        case .invalidConfiguration: .invalidConfiguration
        case .invalidInputFormat: .invalidInputFormat
        case .sampleTooQuiet: .sampleTooQuiet
        case .sampleClipped: .sampleClipped
        case .recordingFailed: .recordingFailed
        }
    }
}
