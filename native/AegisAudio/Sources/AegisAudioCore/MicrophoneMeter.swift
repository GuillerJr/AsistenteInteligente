@preconcurrency import AVFoundation
import Foundation

public enum MicrophonePermission: String, Codable, Sendable {
    case authorized
    case denied
    case restricted
    case notDetermined = "not_determined"
    case unknown

    public static var current: MicrophonePermission {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            .authorized
        case .denied:
            .denied
        case .restricted:
            .restricted
        case .notDetermined:
            .notDetermined
        @unknown default:
            .unknown
        }
    }
}

public struct AudioHelperStatus: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let type: String
    public let state: String
    public let permission: MicrophonePermission
    public let errorCode: String?

    public init(state: String, permission: MicrophonePermission, errorCode: String? = nil) {
        schemaVersion = "1.0"
        type = "audio.status"
        self.state = state
        self.permission = permission
        self.errorCode = errorCode
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case state
        case permission
        case errorCode = "error_code"
    }
}

public enum MicrophoneMeterError: Error, Equatable {
    case permissionRequired(MicrophonePermission)
    case invalidInputFormat
}

public final class NDJSONWriter: @unchecked Sendable {
    private let handle: FileHandle
    private let lock = NSLock()
    private let encoder: JSONEncoder

    public init(handle: FileHandle = .standardOutput) {
        self.handle = handle
        encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    }

    public func write<T: Encodable>(_ value: T) throws {
        var data = try encoder.encode(value)
        data.append(0x0A)
        lock.lock()
        defer { lock.unlock() }
        try handle.write(contentsOf: data)
    }
}

private final class MeterProcessor: @unchecked Sendable {
    private let analyzer: AudioMeterAnalyzer
    private let writer: NDJSONWriter
    private let lock = NSLock()
    private let outputQueue = DispatchQueue(label: "ai.aegis.audio-meter-output")
    private var sequence: UInt64 = 0
    private var outputPending = false
    private var voiceActivityDetector = VoiceActivityDetector()

    init(analyzer: AudioMeterAnalyzer, writer: NDJSONWriter) {
        self.analyzer = analyzer
        self.writer = writer
    }

    func process(buffer: AVAudioPCMBuffer, sampleRateHz: Double) {
        guard let channel = buffer.floatChannelData?.pointee else {
            return
        }
        let samples = UnsafeBufferPointer(start: channel, count: Int(buffer.frameLength))
        lock.lock()
        let currentSequence = sequence
        sequence &+= 1
        lock.unlock()

        guard let sample = analyzer.analyze(
            samples: samples,
            sequence: currentSequence,
            monotonicNanoseconds: DispatchTime.now().uptimeNanoseconds,
            sampleRateHz: sampleRateHz
        ) else {
            return
        }
        let speechEvent = voiceActivityDetector.consume(sample)

        lock.lock()
        let isCoalescedMeter = speechEvent == nil
        let shouldPublish = speechEvent != nil || !outputPending
        if isCoalescedMeter && shouldPublish {
            outputPending = true
        }
        lock.unlock()
        guard shouldPublish else {
            return
        }

        outputQueue.async { [self] in
            try? writer.write(AudioMeterEnvelope(sample: sample, speechEvent: speechEvent))
            if isCoalescedMeter {
                markOutputComplete()
            }
        }
    }

    func flush() {
        outputQueue.sync {}
    }

    private func markOutputComplete() {
        lock.lock()
        outputPending = false
        lock.unlock()
    }
}

public final class MicrophoneMeter {
    private let analyzer: AudioMeterAnalyzer
    private let writer: NDJSONWriter

    public init(
        analyzer: AudioMeterAnalyzer = AudioMeterAnalyzer(),
        writer: NDJSONWriter = NDJSONWriter()
    ) {
        self.analyzer = analyzer
        self.writer = writer
    }

    public func run(durationSeconds: TimeInterval, intervalMilliseconds: Int) throws {
        let permission = MicrophonePermission.current
        guard permission == .authorized else {
            throw MicrophoneMeterError.permissionRequired(permission)
        }

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate >= 8_000, format.channelCount > 0 else {
            throw MicrophoneMeterError.invalidInputFormat
        }

        let requestedFrames = Int(format.sampleRate * Double(intervalMilliseconds) / 1_000)
        let bufferSize = AVAudioFrameCount(min(max(requestedFrames, 128), 16_384))
        let processor = MeterProcessor(analyzer: analyzer, writer: writer)
        input.installTap(onBus: 0, bufferSize: bufferSize, format: format) { buffer, _ in
            processor.process(buffer: buffer, sampleRateHz: format.sampleRate)
        }
        var tapInstalled = true
        defer {
            engine.stop()
            if tapInstalled {
                input.removeTap(onBus: 0)
            }
        }

        engine.prepare()
        try engine.start()
        try writer.write(AudioHelperStatus(state: "running", permission: permission))
        Thread.sleep(forTimeInterval: durationSeconds)
        engine.stop()
        input.removeTap(onBus: 0)
        tapInstalled = false
        processor.flush()
        try writer.write(AudioHelperStatus(state: "completed", permission: permission))
    }
}
