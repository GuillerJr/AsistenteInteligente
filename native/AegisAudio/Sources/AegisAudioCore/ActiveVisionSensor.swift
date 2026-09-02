@preconcurrency import AVFoundation
import AppKit
import CoreGraphics
import CoreMedia
import CoreVideo
import Foundation
import OSLog
@preconcurrency import ScreenCaptureKit
@preconcurrency import Vision
#if canImport(FoundationModels)
import FoundationModels
#endif

public enum ActiveVisionSource: String, Codable, Sendable {
    case camera
    case screen
}

public struct ActiveVisionRect: Codable, Equatable, Sendable {
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

public struct ActiveVisionElement: Codable, Equatable, Sendable {
    public let kind: String
    public let label: String
    public let confidence: Double
    public let bounds: ActiveVisionRect

    public init(kind: String, label: String, confidence: Double, bounds: ActiveVisionRect) {
        self.kind = kind
        self.label = label
        self.confidence = confidence
        self.bounds = bounds
    }
}

public struct ActiveVisionObservation: Codable, Equatable, Sendable {
    public let source: ActiveVisionSource
    public let bundleIdentifier: String?
    public let pixelWidth: Int
    public let pixelHeight: Int
    public let frameSequence: UInt64
    public let capturedMonotonicNanoseconds: UInt64
    public let sceneSummary: String
    public let elements: [ActiveVisionElement]

    enum CodingKeys: String, CodingKey {
        case source
        case bundleIdentifier = "bundle_identifier"
        case pixelWidth = "pixel_width"
        case pixelHeight = "pixel_height"
        case frameSequence = "frame_sequence"
        case capturedMonotonicNanoseconds = "captured_monotonic_ns"
        case sceneSummary = "scene_summary"
        case elements
    }
}

public enum ActiveVisionSensorError: Error, Equatable, Sendable {
    case alreadyRunning
    case cameraPermissionDenied
    case cameraUnavailable
    case captureUnavailable
    case invalidPrompt
    case modelUnavailable
    case screenPermissionDenied
    case thermalSuspended
}

private struct ActiveVisionFrame: @unchecked Sendable {
    let source: ActiveVisionSource
    let pixelBuffer: CVPixelBuffer
    let capturedNanoseconds: UInt64
}

private final class ActiveVisionFrameOutput: NSObject, SCStreamOutput,
    AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable
{
    private let lock = NSLock()
    private var minimumIntervalNanoseconds: UInt64 = 500_000_000
    private var lastDeliveryNanoseconds: UInt64 = 0
    var onFrame: (@Sendable (ActiveVisionFrame) -> Void)?

    func setMinimumInterval(_ interval: UInt64) {
        lock.withLock { minimumIntervalNanoseconds = interval }
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .screen else { return }
        deliver(sampleBuffer, source: .screen)
    }

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        deliver(sampleBuffer, source: .camera)
    }

    private func deliver(_ sampleBuffer: CMSampleBuffer, source: ActiveVisionSource) {
        guard
            CMSampleBufferIsValid(sampleBuffer),
            let buffer = CMSampleBufferGetImageBuffer(sampleBuffer)
        else {
            return
        }
        let now = DispatchTime.now().uptimeNanoseconds
        let shouldDeliver = lock.withLock { () -> Bool in
            guard now >= lastDeliveryNanoseconds + minimumIntervalNanoseconds else {
                return false
            }
            lastDeliveryNanoseconds = now
            return true
        }
        guard shouldDeliver else { return }
        onFrame?(ActiveVisionFrame(
            source: source,
            pixelBuffer: buffer,
            capturedNanoseconds: now
        ))
    }
}

public actor ActiveVisionSensor {
    public typealias ObservationHandler = @Sendable (ActiveVisionObservation) async -> Void

    private let logger = Logger(subsystem: "ai.aegis.audio-core", category: "ActiveVision")
    private let output = ActiveVisionFrameOutput()
    private let observationHandler: ObservationHandler
    private var screenStream: SCStream?
    private var cameraSession: AVCaptureSession?
    private var latestFrame: ActiveVisionFrame?
    private var latestObservation: ActiveVisionObservation?
    private var frameSequence: UInt64 = 0
    private var frameTask: Task<Void, Never>?
    private var pendingFrame: ActiveVisionFrame?
    private var thermalObserver: NSObjectProtocol?

    public init(observationHandler: @escaping ObservationHandler) {
        self.observationHandler = observationHandler
        output.onFrame = { [weak self] frame in
            Task { await self?.enqueue(frame) }
        }
    }

    deinit {
        if let thermalObserver {
            NotificationCenter.default.removeObserver(thermalObserver)
        }
        frameTask?.cancel()
    }

    public func startScreen() async throws {
        guard screenStream == nil else { throw ActiveVisionSensorError.alreadyRunning }
        installThermalObserverIfNeeded()
        try requireThermalCapacity()
        guard CGPreflightScreenCaptureAccess() else {
            throw ActiveVisionSensorError.screenPermissionDenied
        }
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: true
            )
        } catch {
            throw ActiveVisionSensorError.screenPermissionDenied
        }
        guard let display = content.displays.first else {
            throw ActiveVisionSensorError.captureUnavailable
        }
        let filter = SCContentFilter(
            display: display,
            excludingApplications: [],
            exceptingWindows: []
        )
        let configuration = SCStreamConfiguration()
        let scale = min(1.0, 1_280.0 / Double(max(display.width, 1)))
        configuration.width = max(1, Int(Double(display.width) * scale))
        configuration.height = max(1, Int(Double(display.height) * scale))
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 4)
        configuration.queueDepth = 2
        configuration.showsCursor = false
        configuration.capturesAudio = false
        let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
        do {
            try stream.addStreamOutput(
                output,
                type: .screen,
                sampleHandlerQueue: DispatchQueue(
                    label: "ai.aegis.active-vision.screen",
                    qos: .utility
                )
            )
            try await stream.startCapture()
        } catch {
            throw ActiveVisionSensorError.captureUnavailable
        }
        screenStream = stream
        applyThermalSamplingPolicy()
    }

    public func stopScreen() async {
        guard let stream = screenStream else { return }
        screenStream = nil
        do {
            try await stream.stopCapture()
        } catch {
            logger.error("screen_stop_failed privacy=true")
        }
    }

    public func startCamera() async throws {
        guard cameraSession == nil else { throw ActiveVisionSensorError.alreadyRunning }
        installThermalObserverIfNeeded()
        try requireThermalCapacity()
        let authorized: Bool
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            authorized = true
        case .notDetermined:
            authorized = await AVCaptureDevice.requestAccess(for: .video)
        case .denied, .restricted:
            authorized = false
        @unknown default:
            authorized = false
        }
        guard authorized else { throw ActiveVisionSensorError.cameraPermissionDenied }
        guard let device = AVCaptureDevice.default(for: .video) else {
            throw ActiveVisionSensorError.cameraUnavailable
        }
        let session = AVCaptureSession()
        session.beginConfiguration()
        session.sessionPreset = .vga640x480
        do {
            let input = try AVCaptureDeviceInput(device: device)
            guard session.canAddInput(input) else {
                throw ActiveVisionSensorError.cameraUnavailable
            }
            session.addInput(input)
            let video = AVCaptureVideoDataOutput()
            video.alwaysDiscardsLateVideoFrames = true
            video.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String:
                    Int(kCVPixelFormatType_32BGRA),
            ]
            guard session.canAddOutput(video) else {
                throw ActiveVisionSensorError.cameraUnavailable
            }
            session.addOutput(video)
            video.setSampleBufferDelegate(
                output,
                queue: DispatchQueue(label: "ai.aegis.active-vision.camera", qos: .utility)
            )
        } catch let error as ActiveVisionSensorError {
            session.commitConfiguration()
            throw error
        } catch {
            session.commitConfiguration()
            throw ActiveVisionSensorError.cameraUnavailable
        }
        session.commitConfiguration()
        cameraSession = session
        applyThermalSamplingPolicy()
        DispatchQueue.global(qos: .utility).async { session.startRunning() }
    }

    public func stopCamera() {
        guard let session = cameraSession else { return }
        cameraSession = nil
        DispatchQueue.global(qos: .utility).async { session.stopRunning() }
    }

    public func stop() async {
        await stopScreen()
        stopCamera()
        pendingFrame = nil
        latestFrame = nil
        latestObservation = nil
        frameTask?.cancel()
        frameTask = nil
    }

    public func currentObservation() -> ActiveVisionObservation? {
        latestObservation
    }

    public func reason(
        prompt: String,
        activeBundleIdentifier: String?
    ) async throws -> String {
        guard
            !prompt.isEmpty,
            prompt.utf8.count <= 4_096,
            !prompt.unicodeScalars.contains(where: { $0.value == 0 }),
            let frame = latestFrame,
            let observation = latestObservation
        else {
            throw ActiveVisionSensorError.invalidPrompt
        }
        try requireThermalCapacity()
        #if canImport(FoundationModels)
        #if AEGIS_FOUNDATION_MODELS_VISION
        if #available(macOS 27.0, *) {
            return try await reasonWithImage(
                prompt: prompt,
                frame: frame,
                activeBundleIdentifier: activeBundleIdentifier
            )
        }
        #endif
        if #available(macOS 26.0, *) {
            let helper = LocalInferenceHelper()
            let ocrContext = try LocalOCRPerception.context(from: frame.pixelBuffer)
            let spatialContext = Self.structuredContext(observation)
            return try await helper.generateDraft(
                instructions: Self.instructions(for: activeBundleIdentifier),
                prompt: "\(prompt)\n\n\(ocrContext)\n\n\(spatialContext)",
                maximumResponseTokens: 384,
                temperature: 0.1,
                onSnapshot: { _ in }
            )
        }
        #endif
        throw ActiveVisionSensorError.modelUnavailable
    }

    private func enqueue(_ frame: ActiveVisionFrame) {
        pendingFrame = frame
        guard frameTask == nil else { return }
        frameTask = Task(priority: .utility) { [weak self] in
            guard let self else { return }
            await self.processPendingFrames()
        }
    }

    private func processPendingFrames() async {
        while !Task.isCancelled, let frame = pendingFrame {
            pendingFrame = nil
            do {
                let observation = try Self.analyze(
                    frame,
                    sequence: frameSequence,
                    bundleIdentifier: Self.frontmostBundleIdentifier()
                )
                frameSequence &+= 1
                latestFrame = frame
                latestObservation = observation
                await observationHandler(observation)
            } catch {
                logger.error("vision_analysis_failed privacy=true")
            }
        }
        frameTask = nil
    }

    private func thermalStateChanged() async {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal, .fair:
            applyThermalSamplingPolicy()
        case .serious, .critical:
            await stopScreen()
            stopCamera()
            latestFrame = nil
            logger.notice("vision_thermal_pause privacy=true")
        @unknown default:
            await stop()
        }
    }

    private func installThermalObserverIfNeeded() {
        guard thermalObserver == nil else { return }
        thermalObserver = NotificationCenter.default.addObserver(
            forName: ProcessInfo.thermalStateDidChangeNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            Task { await self?.thermalStateChanged() }
        }
    }

    private func applyThermalSamplingPolicy() {
        let interval: UInt64 = ProcessInfo.processInfo.thermalState == .nominal
            ? 500_000_000
            : 1_000_000_000
        output.setMinimumInterval(interval)
    }

    private func requireThermalCapacity() throws {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal, .fair:
            return
        case .serious, .critical:
            throw ActiveVisionSensorError.thermalSuspended
        @unknown default:
            throw ActiveVisionSensorError.thermalSuspended
        }
    }

    private nonisolated static func analyze(
        _ frame: ActiveVisionFrame,
        sequence: UInt64,
        bundleIdentifier: String?
    ) throws -> ActiveVisionObservation {
        let width = CVPixelBufferGetWidth(frame.pixelBuffer)
        let height = CVPixelBufferGetHeight(frame.pixelBuffer)
        let text = VNRecognizeTextRequest()
        text.recognitionLevel = .fast
        text.usesLanguageCorrection = false
        text.recognitionLanguages = ["es-ES", "en-US"]
        text.minimumTextHeight = 0.012
        let rectangles = VNDetectRectanglesRequest()
        rectangles.maximumObservations = 32
        rectangles.minimumConfidence = 0.55
        rectangles.minimumSize = 0.015
        let handler = VNImageRequestHandler(cvPixelBuffer: frame.pixelBuffer, options: [:])
        try handler.perform([text, rectangles])
        var elements: [ActiveVisionElement] = []
        for observation in (text.results ?? []).prefix(96) {
            guard let candidate = observation.topCandidates(1).first else { continue }
            elements.append(ActiveVisionElement(
                kind: "text",
                label: String(candidate.string.prefix(512)),
                confidence: Double(candidate.confidence),
                bounds: normalizedTopLeft(observation.boundingBox)
            ))
        }
        for observation in (rectangles.results ?? []).prefix(32) {
            elements.append(ActiveVisionElement(
                kind: "rectangle",
                label: "interactive-region",
                confidence: Double(observation.confidence),
                bounds: normalizedTopLeft(observation.boundingBox)
            ))
        }
        return ActiveVisionObservation(
            source: frame.source,
            bundleIdentifier: bundleIdentifier,
            pixelWidth: width,
            pixelHeight: height,
            frameSequence: sequence,
            capturedMonotonicNanoseconds: frame.capturedNanoseconds,
            sceneSummary: "",
            elements: Array(elements.prefix(128))
        )
    }

    private nonisolated static func normalizedTopLeft(_ box: CGRect) -> ActiveVisionRect {
        ActiveVisionRect(
            x: Double(box.minX),
            y: Double(1.0 - box.maxY),
            width: Double(box.width),
            height: Double(box.height)
        )
    }

    private nonisolated static func frontmostBundleIdentifier() -> String? {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier
    }

    private nonisolated static func instructions(for bundleIdentifier: String?) -> String {
        switch bundleIdentifier?.lowercased() {
        case "com.google.chrome", "com.apple.safari":
            "Eres el perfil Browser Expert local. Describe solo estado visible y acciones verificables."
        case "com.apple.finder":
            "Eres el perfil Finder Expert local. No propongas acciones destructivas sin confirmar."
        case "com.apple.mail":
            "Eres el perfil Mail Expert local. Distingue lectura, borrador y envío con precisión."
        default:
            "Eres el sensor visual local de Jarvis. Razona solo sobre evidencia visible y OCR."
        }
    }

    private nonisolated static func structuredContext(
        _ observation: ActiveVisionObservation
    ) -> String {
        let lines = observation.elements.prefix(96).map { element in
            "\(element.kind): \(element.label) @ "
                + "(\(String(format: "%.3f", element.bounds.x)),"
                + "\(String(format: "%.3f", element.bounds.y)))"
        }
        return (["# Evidencia visual local"] + lines).joined(separator: "\n")
    }

    #if canImport(FoundationModels) && AEGIS_FOUNDATION_MODELS_VISION
    @available(macOS 27.0, *)
    private func reasonWithImage(
        prompt: String,
        frame: ActiveVisionFrame,
        activeBundleIdentifier: String?
    ) async throws -> String {
        guard SystemLanguageModel.default.isAvailable else {
            throw ActiveVisionSensorError.modelUnavailable
        }
        let session = LanguageModelSession(
            model: .default,
            tools: [],
            instructions: Self.instructions(for: activeBundleIdentifier)
        )
        let response = try await session.respond {
            prompt
            Attachment(frame.pixelBuffer).label("active-vision-frame")
        }
        return response.content
    }
    #endif
}

public struct SignedActiveVisionBridge: Sendable {
    private let secret: Data

    public init(secret: Data) throws {
        guard secret.count == 32 else { throw LocalIPCError.invalidCredential }
        self.secret = secret
    }

    public func submit(_ observation: ActiveVisionObservation) async throws -> String {
        let encoded = try JSONEncoder().encode(observation)
        guard
            encoded.count <= 48_000,
            let payload = try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        else {
            throw ActiveVisionSensorError.captureUnavailable
        }
        let secret = self.secret
        return try await Task.detached(priority: .utility) {
            let response = try LocalIPCClient(secret: secret).call(
                method: "vision.active.observe",
                payload: payload
            )
            guard
                response.ok,
                let digest = response.payload["state_sha256"] as? String,
                digest.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil
            else {
                throw ActiveVisionSensorError.captureUnavailable
            }
            return digest
        }.value
    }
}
