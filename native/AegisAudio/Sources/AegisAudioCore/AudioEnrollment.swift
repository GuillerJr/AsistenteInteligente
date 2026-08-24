@preconcurrency import AVFoundation
import Foundation

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
