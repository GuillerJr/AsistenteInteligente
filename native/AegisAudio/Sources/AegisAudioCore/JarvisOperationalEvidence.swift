import Foundation

public struct JarvisOperationalEvidenceSnapshot: Codable, Equatable, Sendable {
    public static let schemaVersion = "1.0"
    public static let maximumCounter = 1_000_000_000

    public let schemaVersion: String
    public private(set) var voiceTurns: Int
    public private(set) var ownerVerifiedVoiceTurns: Int
    public private(set) var lastVoiceAt: Date?
    public private(set) var lastVoiceFirstPartialMilliseconds: Int?
    public private(set) var lastVoiceTotalMilliseconds: Int?
    public private(set) var screenCaptures: Int
    public private(set) var lastScreenCaptureAt: Date?
    public private(set) var lastScreenCaptureMilliseconds: Int?
    public private(set) var lastScreenPayloadBytes: Int?
    public private(set) var computerActions: Int
    public private(set) var verifiedComputerActions: Int
    public private(set) var lastComputerActionAt: Date?
    public private(set) var lastComputerActionMilliseconds: Int?

    public init() {
        schemaVersion = Self.schemaVersion
        voiceTurns = 0
        ownerVerifiedVoiceTurns = 0
        lastVoiceAt = nil
        lastVoiceFirstPartialMilliseconds = nil
        lastVoiceTotalMilliseconds = nil
        screenCaptures = 0
        lastScreenCaptureAt = nil
        lastScreenCaptureMilliseconds = nil
        lastScreenPayloadBytes = nil
        computerActions = 0
        verifiedComputerActions = 0
        lastComputerActionAt = nil
        lastComputerActionMilliseconds = nil
    }

    public var isValid: Bool {
        guard
            schemaVersion == Self.schemaVersion,
            (0 ... Self.maximumCounter).contains(voiceTurns),
            (0 ... voiceTurns).contains(ownerVerifiedVoiceTurns),
            (0 ... Self.maximumCounter).contains(screenCaptures),
            (0 ... Self.maximumCounter).contains(computerActions),
            (0 ... computerActions).contains(verifiedComputerActions),
            Self.validLatency(lastVoiceFirstPartialMilliseconds, maximum: 600_000),
            Self.validLatency(lastVoiceTotalMilliseconds, maximum: 600_000),
            Self.validLatency(lastScreenCaptureMilliseconds, maximum: 60_000),
            Self.validLatency(lastComputerActionMilliseconds, maximum: 22_000),
            lastScreenPayloadBytes.map({ (1 ... 32_768).contains($0) }) ?? true
        else {
            return false
        }
        if let first = lastVoiceFirstPartialMilliseconds,
           let total = lastVoiceTotalMilliseconds,
           first > total
        {
            return false
        }
        return true
    }

    mutating func recordVoiceTurn(
        ownerVerified: Bool,
        firstPartialMilliseconds: Int?,
        totalMilliseconds: Int,
        at date: Date
    ) throws {
        guard
            Self.validLatency(firstPartialMilliseconds, maximum: 600_000),
            (0 ... 600_000).contains(totalMilliseconds),
            firstPartialMilliseconds.map({ $0 <= totalMilliseconds }) ?? true
        else {
            throw JarvisOperationalEvidenceError.invalidMetric
        }
        voiceTurns = Self.increment(voiceTurns)
        if ownerVerified {
            ownerVerifiedVoiceTurns = Self.increment(ownerVerifiedVoiceTurns)
        }
        lastVoiceAt = date
        lastVoiceFirstPartialMilliseconds = firstPartialMilliseconds
        lastVoiceTotalMilliseconds = totalMilliseconds
    }

    mutating func recordScreenCapture(
        milliseconds: Int,
        payloadBytes: Int,
        at date: Date
    ) throws {
        guard
            (0 ... 60_000).contains(milliseconds),
            (1 ... 32_768).contains(payloadBytes)
        else {
            throw JarvisOperationalEvidenceError.invalidMetric
        }
        screenCaptures = Self.increment(screenCaptures)
        lastScreenCaptureAt = date
        lastScreenCaptureMilliseconds = milliseconds
        lastScreenPayloadBytes = payloadBytes
    }

    mutating func recordComputerAction(
        verified: Bool,
        milliseconds: Int,
        at date: Date
    ) throws {
        guard (0 ... 22_000).contains(milliseconds) else {
            throw JarvisOperationalEvidenceError.invalidMetric
        }
        computerActions = Self.increment(computerActions)
        if verified {
            verifiedComputerActions = Self.increment(verifiedComputerActions)
        }
        lastComputerActionAt = date
        lastComputerActionMilliseconds = milliseconds
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case voiceTurns = "voice_turns"
        case ownerVerifiedVoiceTurns = "owner_verified_voice_turns"
        case lastVoiceAt = "last_voice_at"
        case lastVoiceFirstPartialMilliseconds = "last_voice_first_partial_ms"
        case lastVoiceTotalMilliseconds = "last_voice_total_ms"
        case screenCaptures = "screen_captures"
        case lastScreenCaptureAt = "last_screen_capture_at"
        case lastScreenCaptureMilliseconds = "last_screen_capture_ms"
        case lastScreenPayloadBytes = "last_screen_payload_bytes"
        case computerActions = "computer_actions"
        case verifiedComputerActions = "verified_computer_actions"
        case lastComputerActionAt = "last_computer_action_at"
        case lastComputerActionMilliseconds = "last_computer_action_ms"
    }

    private static func increment(_ value: Int) -> Int {
        min(value + 1, maximumCounter)
    }

    private static func validLatency(_ value: Int?, maximum: Int) -> Bool {
        value.map({ (0 ... maximum).contains($0) }) ?? true
    }
}

public enum JarvisOperationalEvidenceError: Error, Equatable, Sendable {
    case invalidMetric
    case invalidStoredEvidence
    case unsafeDestination
}

public actor JarvisOperationalEvidenceRecorder {
    public static let shared = JarvisOperationalEvidenceRecorder()

    private let destination: URL?
    private var loaded = false
    private var snapshot = JarvisOperationalEvidenceSnapshot()

    public init(destination: URL? = nil) {
        self.destination = destination
    }

    public func recordVoiceTurn(
        ownerVerified: Bool,
        firstPartialMilliseconds: Int?,
        totalMilliseconds: Int,
        at date: Date = Date()
    ) throws {
        try loadIfNeeded()
        try snapshot.recordVoiceTurn(
            ownerVerified: ownerVerified,
            firstPartialMilliseconds: firstPartialMilliseconds,
            totalMilliseconds: totalMilliseconds,
            at: date
        )
        try persist()
    }

    public func recordScreenCapture(
        milliseconds: Int,
        payloadBytes: Int,
        at date: Date = Date()
    ) throws {
        try loadIfNeeded()
        try snapshot.recordScreenCapture(
            milliseconds: milliseconds,
            payloadBytes: payloadBytes,
            at: date
        )
        try persist()
    }

    public func recordComputerAction(
        verified: Bool,
        milliseconds: Int,
        at date: Date = Date()
    ) throws {
        try loadIfNeeded()
        try snapshot.recordComputerAction(
            verified: verified,
            milliseconds: milliseconds,
            at: date
        )
        try persist()
    }

    public func current() throws -> JarvisOperationalEvidenceSnapshot {
        try loadIfNeeded()
        return snapshot
    }

    public static func defaultURL(
        fileManager: FileManager = .default
    ) throws -> URL {
        guard let applicationSupport = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw JarvisOperationalEvidenceError.unsafeDestination
        }
        return applicationSupport
            .appendingPathComponent("Aegis", isDirectory: true)
            .appendingPathComponent("runtime-evidence.json", isDirectory: false)
    }

    private func loadIfNeeded(fileManager: FileManager = .default) throws {
        guard !loaded else { return }
        let target = try destination ?? Self.defaultURL(fileManager: fileManager)
        try Self.validateRegularDestination(target, fileManager: fileManager)
        if fileManager.fileExists(atPath: target.path) {
            let values = try target.resourceValues(forKeys: [.fileSizeKey])
            guard let size = values.fileSize, (1 ... 16_384).contains(size) else {
                throw JarvisOperationalEvidenceError.invalidStoredEvidence
            }
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let stored: JarvisOperationalEvidenceSnapshot
            do {
                stored = try decoder.decode(
                    JarvisOperationalEvidenceSnapshot.self,
                    from: Data(contentsOf: target, options: [.mappedIfSafe])
                )
            } catch {
                throw JarvisOperationalEvidenceError.invalidStoredEvidence
            }
            guard stored.isValid else {
                throw JarvisOperationalEvidenceError.invalidStoredEvidence
            }
            snapshot = stored
        }
        loaded = true
    }

    private func persist(fileManager: FileManager = .default) throws {
        guard snapshot.isValid else {
            throw JarvisOperationalEvidenceError.invalidStoredEvidence
        }
        let target = try destination ?? Self.defaultURL(fileManager: fileManager)
        let directory = target.deletingLastPathComponent()
        try Self.validateRegularDestination(target, fileManager: fileManager)
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
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        var encoded = try encoder.encode(snapshot)
        encoded.append(0x0A)
        guard encoded.count <= 16_384 else {
            throw JarvisOperationalEvidenceError.invalidStoredEvidence
        }
        try encoded.write(to: target, options: .atomic)
        try Self.validateRegularDestination(target, fileManager: fileManager)
        try fileManager.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: target.path
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
            throw JarvisOperationalEvidenceError.unsafeDestination
        }
    }
}
