@preconcurrency import AVFoundation
import Darwin
import Foundation

enum EnrollmentPendingFileError: Error, Equatable, Sendable {
    case invalidConfiguration
    case unsafeFile
}

enum EnrollmentPendingFiles {
    static let maximumFileBytes = 5 * 1_024 * 1_024
    static let minimumCleanupAge: TimeInterval = 60
    private static let prefix = ".pending-"
    private static let suffix = ".caf"
    private static let maximumFiles = 16

    static func makeURL(in rootURL: URL) -> URL {
        rootURL.appending(path: "\(prefix)\(UUID().uuidString)\(suffix)")
    }

    static func secureForRecording(_ fileURL: URL) throws {
        guard isCanonical(fileURL) else {
            throw EnrollmentPendingFileError.unsafeFile
        }
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: fileURL.path
        )
        _ = try validate(fileURL)
    }

    @discardableResult
    static func removeAbandoned(
        in rootURL: URL,
        now: Date = Date(),
        minimumAge: TimeInterval = minimumCleanupAge
    ) throws -> Int {
        guard minimumAge.isFinite, (10 ... 3_600).contains(minimumAge) else {
            throw EnrollmentPendingFileError.invalidConfiguration
        }
        let entries = try FileManager.default.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [
                .contentModificationDateKey,
                .fileSizeKey,
                .isRegularFileKey,
                .isSymbolicLinkKey,
            ]
        )
        let candidates = entries.filter { $0.lastPathComponent.hasPrefix(prefix) }
        guard candidates.count <= maximumFiles else {
            throw EnrollmentPendingFileError.unsafeFile
        }

        var removed = 0
        for candidate in candidates {
            guard
                candidate.deletingLastPathComponent().standardizedFileURL
                    == rootURL.standardizedFileURL,
                isCanonical(candidate)
            else {
                throw EnrollmentPendingFileError.unsafeFile
            }
            let modified = try validate(candidate)
            let age = now.timeIntervalSince(modified)
            if age.isFinite, age >= minimumAge {
                try FileManager.default.removeItem(at: candidate)
                removed += 1
            }
        }
        return removed
    }

    private static func validate(_ fileURL: URL) throws -> Date {
        let values = try fileURL.resourceValues(forKeys: [
            .contentModificationDateKey,
            .fileSizeKey,
            .isRegularFileKey,
            .isSymbolicLinkKey,
        ])
        let attributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)
        let permissions = attributes[.posixPermissions] as? Int
        let owner = (attributes[.ownerAccountID] as? NSNumber)?.intValue
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true,
            let size = values.fileSize,
            (0 ... maximumFileBytes).contains(size),
            let modified = values.contentModificationDate,
            let permissions,
            permissions & 0o077 == 0,
            owner == Int(getuid())
        else {
            throw EnrollmentPendingFileError.unsafeFile
        }
        return modified
    }

    private static func isCanonical(_ fileURL: URL) -> Bool {
        let name = fileURL.lastPathComponent
        guard name.hasPrefix(prefix), name.hasSuffix(suffix) else { return false }
        let start = name.index(name.startIndex, offsetBy: prefix.count)
        let end = name.index(name.endIndex, offsetBy: -suffix.count)
        return UUID(uuidString: String(name[start ..< end])) != nil
    }
}

enum EnrollmentAudioCaptureError: Error, Equatable, Sendable {
    case permissionRequired(MicrophonePermission)
    case invalidConfiguration
    case invalidInputFormat
    case sampleTooQuiet
    case sampleClipped
    case recordingFailed
}

struct EnrollmentSampleQualityAccumulator: Sendable {
    static let audibleRMSThreshold: Float = 0.008

    private(set) var analyzedFrames = 0
    private(set) var audibleFrames = 0
    private(set) var clipped = false

    mutating func consume(rms: Float, peak: Float, frameCount: Int) {
        guard
            rms.isFinite,
            peak.isFinite,
            (0 ... 1).contains(rms),
            (0 ... 1).contains(peak),
            frameCount > 0
        else {
            return
        }
        analyzedFrames += frameCount
        if rms >= Self.audibleRMSThreshold {
            audibleFrames += frameCount
        }
        if peak >= 0.999 {
            clipped = true
        }
    }

    func validate(
        requiresAudibleVoice: Bool,
        minimumAnalyzedFrames: Int,
        minimumAudibleFrames: Int
    ) throws {
        guard analyzedFrames >= minimumAnalyzedFrames else {
            throw EnrollmentAudioCaptureError.recordingFailed
        }
        guard !clipped else {
            throw EnrollmentAudioCaptureError.sampleClipped
        }
        if requiresAudibleVoice, audibleFrames < minimumAudibleFrames {
            throw EnrollmentAudioCaptureError.sampleTooQuiet
        }
    }
}

private final class EnrollmentAudioWriter: @unchecked Sendable {
    private let file: AVAudioFile
    private let analyzer = AudioMeterAnalyzer(
        voiceThresholdRMS: EnrollmentSampleQualityAccumulator.audibleRMSThreshold
    )
    private let lock = NSLock()
    private var frameCount: AVAudioFramePosition = 0
    private var sequence: UInt64 = 0
    private var quality = EnrollmentSampleQualityAccumulator()
    private var failed = false

    init(file: AVAudioFile) {
        self.file = file
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.withLock {
            if let channel = buffer.floatChannelData?.pointee {
                let samples = UnsafeBufferPointer(
                    start: channel,
                    count: Int(buffer.frameLength)
                )
                if let sample = analyzer.analyze(
                    samples: samples,
                    sequence: sequence,
                    monotonicNanoseconds: DispatchTime.now().uptimeNanoseconds,
                    sampleRateHz: buffer.format.sampleRate
                ) {
                    quality.consume(
                        rms: sample.rms,
                        peak: sample.peak,
                        frameCount: sample.frameCount
                    )
                }
                sequence &+= 1
            }
            do {
                try file.write(from: buffer)
                frameCount += AVAudioFramePosition(buffer.frameLength)
            } catch {
                failed = true
            }
        }
    }

    func validate(
        requiresAudibleVoice: Bool,
        minimumFrames: AVAudioFramePosition,
        minimumAudibleFrames: Int
    ) throws {
        try lock.withLock {
            guard !failed, frameCount >= minimumFrames else {
                throw EnrollmentAudioCaptureError.recordingFailed
            }
            try quality.validate(
                requiresAudibleVoice: requiresAudibleVoice,
                minimumAnalyzedFrames: Int(minimumFrames),
                minimumAudibleFrames: minimumAudibleFrames
            )
        }
    }
}

enum EnrollmentAudioCapture {
    static func record(
        to temporaryURL: URL,
        durationSeconds: TimeInterval,
        requiresAudibleVoice: Bool
    ) throws {
        guard durationSeconds.isFinite, (1 ... 8).contains(durationSeconds) else {
            throw EnrollmentAudioCaptureError.invalidConfiguration
        }
        let permission = MicrophonePermission.current
        guard permission == .authorized else {
            throw EnrollmentAudioCaptureError.permissionRequired(permission)
        }

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate >= 8_000, format.channelCount > 0 else {
            throw EnrollmentAudioCaptureError.invalidInputFormat
        }
        let file: AVAudioFile
        do {
            file = try AVAudioFile(forWriting: temporaryURL, settings: format.settings)
            try EnrollmentPendingFiles.secureForRecording(temporaryURL)
        } catch {
            throw EnrollmentAudioCaptureError.recordingFailed
        }
        let writer = EnrollmentAudioWriter(file: file)
        input.installTap(onBus: 0, bufferSize: 2_048, format: format) { buffer, _ in
            writer.append(buffer)
        }
        var tapInstalled = true
        defer {
            engine.stop()
            if tapInstalled {
                input.removeTap(onBus: 0)
            }
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            throw EnrollmentAudioCaptureError.recordingFailed
        }
        Thread.sleep(forTimeInterval: durationSeconds)
        engine.stop()
        input.removeTap(onBus: 0)
        tapInstalled = false

        try writer.validate(
            requiresAudibleVoice: requiresAudibleVoice,
            minimumFrames: AVAudioFramePosition(format.sampleRate * 0.4),
            minimumAudibleFrames: Int(format.sampleRate * 0.12)
        )
    }
}
