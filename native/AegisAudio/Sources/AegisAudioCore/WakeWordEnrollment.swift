import Foundation

public enum WakeWordEnrollmentLabel: String, CaseIterable, Sendable {
    case jarvis
    case background
}

public enum WakeWordEnrollmentGuidance {
    public static func instruction(
        label: WakeWordEnrollmentLabel,
        acceptedCount: Int
    ) -> String {
        let count = max(acceptedCount, 0)
        return switch (label, count) {
        case (.jarvis, 0 ..< 5):
            "Voz normal, a tu distancia habitual"
        case (.jarvis, 5 ..< 10):
            "Voz ligeramente más baja"
        case (.jarvis, 10 ..< 15):
            "Cambia la distancia o gira un poco la cabeza"
        case (.jarvis, 15 ..< 20):
            "Voz normal con el ruido cotidiano presente"
        case (.jarvis, _):
            "Añade otra variación natural"
        case (.background, 0 ..< 5):
            "Captura silencio real de la habitación"
        case (.background, 5 ..< 10):
            "Captura ruido cotidiano sin hablar"
        case (.background, 10 ..< 15):
            "Habla con normalidad sin usar la palabra de activación"
        case (.background, 15 ..< 20):
            "Di palabras parecidas: “Javier”, “viernes” o “jardín”"
        case (.background, _):
            "Añade otro sonido habitual distinto"
        }
    }
}

public struct WakeWordEnrollmentProgress: Equatable, Sendable {
    public static let targetPerLabel = 20

    public let jarvisCount: Int
    public let backgroundCount: Int

    public var isReady: Bool {
        jarvisCount >= Self.targetPerLabel && backgroundCount >= Self.targetPerLabel
    }

    public init(jarvisCount: Int, backgroundCount: Int) {
        self.jarvisCount = jarvisCount
        self.backgroundCount = backgroundCount
    }
}

public enum WakeWordEnrollmentError: Error, Equatable, Sendable {
    case permissionRequired(MicrophonePermission)
    case unsafeStorage
    case capacityReached
    case invalidConfiguration
    case invalidInputFormat
    case sampleTooQuiet
    case sampleClipped
    case recordingFailed
}

typealias WakeWordSampleQualityAccumulator = EnrollmentSampleQualityAccumulator

extension EnrollmentSampleQualityAccumulator {
    func validate(
        label: WakeWordEnrollmentLabel,
        minimumAnalyzedFrames: Int,
        minimumAudibleFrames: Int
    ) throws {
        do {
            try validate(
                requiresAudibleVoice: label == .jarvis,
                minimumAnalyzedFrames: minimumAnalyzedFrames,
                minimumAudibleFrames: minimumAudibleFrames
            )
        } catch let error as EnrollmentAudioCaptureError {
            throw WakeWordEnrollmentError(error)
        }
    }
}

struct WakeWordEnrollmentStore: Sendable {
    static let maximumSamplesPerLabel = 100
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
        return base.appending(path: "Aegis/WakeWordEnrollment", directoryHint: .isDirectory)
    }

    func prepare() throws {
        let manager = FileManager.default
        try prepareDirectory(rootURL, manager: manager)
        for label in WakeWordEnrollmentLabel.allCases {
            try prepareDirectory(labelURL(label), manager: manager)
        }
    }

    func progress() throws -> WakeWordEnrollmentProgress {
        try prepare()
        return WakeWordEnrollmentProgress(
            jarvisCount: try count(.jarvis),
            backgroundCount: try count(.background)
        )
    }

    func clearSamples() throws -> WakeWordEnrollmentProgress {
        try prepare()
        let files = try WakeWordEnrollmentLabel.allCases.flatMap(validatedFiles)
        for file in files {
            try FileManager.default.removeItem(at: file)
        }
        return try progress()
    }

    func makeTemporaryURL() -> URL {
        rootURL.appending(path: ".pending-\(UUID().uuidString).caf")
    }

    func commit(_ temporaryURL: URL, label: WakeWordEnrollmentLabel) throws {
        guard temporaryURL.deletingLastPathComponent().standardizedFileURL == rootURL else {
            throw WakeWordEnrollmentError.unsafeStorage
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
            try count(label) < Self.maximumSamplesPerLabel
        else {
            throw WakeWordEnrollmentError.recordingFailed
        }
        let destination = labelURL(label).appending(path: "\(UUID().uuidString).caf")
        guard !manager.fileExists(atPath: destination.path) else {
            throw WakeWordEnrollmentError.unsafeStorage
        }
        try manager.moveItem(at: temporaryURL, to: destination)
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
    }

    private func count(_ label: WakeWordEnrollmentLabel) throws -> Int {
        try validatedFiles(label).count
    }

    private func validatedFiles(_ label: WakeWordEnrollmentLabel) throws -> [URL] {
        let files = try FileManager.default.contentsOfDirectory(
            at: labelURL(label),
            includingPropertiesForKeys: [
                .fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey,
            ],
            options: [.skipsHiddenFiles]
        )
        guard files.count <= Self.maximumSamplesPerLabel else {
            throw WakeWordEnrollmentError.capacityReached
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
                throw WakeWordEnrollmentError.unsafeStorage
            }
        }
        return files
    }

    private func labelURL(_ label: WakeWordEnrollmentLabel) -> URL {
        rootURL.appending(path: label.rawValue, directoryHint: .isDirectory)
    }

    private func prepareDirectory(_ url: URL, manager: FileManager) throws {
        if !manager.fileExists(atPath: url.path) {
            try manager.createDirectory(
                at: url,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
        }
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        let attributes = try manager.attributesOfItem(atPath: url.path)
        let permissions = attributes[.posixPermissions] as? Int
        guard
            values.isDirectory == true,
            values.isSymbolicLink != true,
            let permissions,
            permissions & 0o077 == 0
        else {
            throw WakeWordEnrollmentError.unsafeStorage
        }
    }
}

public final class WakeWordEnrollmentRecorder {
    private let store: WakeWordEnrollmentStore

    public init() {
        store = WakeWordEnrollmentStore()
    }

    init(store: WakeWordEnrollmentStore) {
        self.store = store
    }

    public func progress() throws -> WakeWordEnrollmentProgress {
        try store.progress()
    }

    public func clearSamples() throws -> WakeWordEnrollmentProgress {
        try store.clearSamples()
    }

    public func record(
        label: WakeWordEnrollmentLabel,
        durationSeconds: TimeInterval = 2
    ) throws -> WakeWordEnrollmentProgress {
        guard durationSeconds.isFinite, (1 ... 3).contains(durationSeconds) else {
            throw WakeWordEnrollmentError.invalidConfiguration
        }
        let permission = MicrophonePermission.current
        guard permission == .authorized else {
            throw WakeWordEnrollmentError.permissionRequired(permission)
        }
        try store.prepare()
        let temporaryURL = store.makeTemporaryURL()
        defer { try? FileManager.default.removeItem(at: temporaryURL) }
        do {
            try EnrollmentAudioCapture.record(
                to: temporaryURL,
                durationSeconds: durationSeconds,
                requiresAudibleVoice: label == .jarvis
            )
        } catch let error as EnrollmentAudioCaptureError {
            throw WakeWordEnrollmentError(error)
        }
        try store.commit(temporaryURL, label: label)
        return try store.progress()
    }
}

private extension WakeWordEnrollmentError {
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
