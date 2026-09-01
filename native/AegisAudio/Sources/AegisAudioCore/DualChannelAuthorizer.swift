@preconcurrency import AVFoundation
import Foundation
@preconcurrency import LocalAuthentication

public enum DualAuthorizationResult: Equatable, Sendable {
    case authorizedByTouchID
    case authorizedByVoice
    case manualConfirmationRequired
}

public struct VoiceAuthorizationEvidence: Sendable {
    public let pcmS16LE: Data
    public let speakerIdentifier: String
    public let speakerConfidence: Double
    public let ownerProfileMatch: Bool
}

public enum SpeakerThresholdPolicy {
    public static let baseline = 0.78
    public static let maximum = 0.95
    public static let adversarialMargin = 0.03

    public static func calibratedThreshold(
        distractorConfidences: [Double],
        ownerConfidences: [Double]
    ) -> Double? {
        guard
            distractorConfidences.count >= 15,
            ownerConfidences.count >= 5,
            distractorConfidences.allSatisfy({ $0.isFinite && (0 ... 1).contains($0) }),
            ownerConfidences.allSatisfy({ $0.isFinite && (0 ... 1).contains($0) })
        else {
            return nil
        }
        let strongestDistractor = distractorConfidences.max() ?? 1
        let threshold = max(baseline, strongestDistractor + adversarialMargin)
        guard
            threshold <= maximum,
            ownerConfidences.allSatisfy({ $0 >= threshold }),
            distractorConfidences.allSatisfy({ $0 < threshold })
        else {
            return nil
        }
        return threshold
    }
}

public struct SpeakerCalibrationReport: Codable, Equatable, Sendable {
    public let accepted: Bool
    public let threshold: Double?
    public let maximumDistractorConfidence: Double
    public let minimumOwnerConfidence: Double
    public let distractorCount: Int
    public let ownerCount: Int
}

public struct SpeakerAdversarialCalibrator: Sendable {
    static let maximumAudioFilesPerClass = 24
    static let maximumAudioBytes = 5 * 1_024 * 1_024

    public let modelURL: URL
    public let distractorDirectory: URL
    public let enrollmentDirectory: URL

    public init(
        modelURL: URL,
        distractorDirectory: URL,
        enrollmentDirectory: URL
    ) {
        self.modelURL = modelURL
        self.distractorDirectory = distractorDirectory
        self.enrollmentDirectory = enrollmentDirectory
    }

    public func calibrate(
        ownerIdentifier: String,
        expectedModelFingerprint: String?
    ) -> Double? {
        analyze(
            ownerIdentifier: ownerIdentifier,
            expectedModelFingerprint: expectedModelFingerprint
        )?.threshold
    }

    public func analyze(
        ownerIdentifier: String,
        expectedModelFingerprint: String?
    ) -> SpeakerCalibrationReport? {
        let distractors = safeAudioFiles(
            in: distractorDirectory,
            minimumCount: 15
        )
        let owners = safeAudioFiles(
            in: enrollmentDirectory.appending(
                path: ownerIdentifier,
                directoryHint: .isDirectory
            ),
            minimumCount: 5
        )
        guard let distractors, let owners else { return nil }
        let distractorScores = distractors.compactMap {
            score(
                audioURL: $0,
                ownerIdentifier: ownerIdentifier,
                expectedModelFingerprint: expectedModelFingerprint
            )
        }
        let ownerScores = owners.compactMap {
            score(
                audioURL: $0,
                ownerIdentifier: ownerIdentifier,
                expectedModelFingerprint: expectedModelFingerprint
            )
        }
        guard
            distractorScores.count == distractors.count,
            ownerScores.count == owners.count
        else {
            return nil
        }
        let threshold = SpeakerThresholdPolicy.calibratedThreshold(
            distractorConfidences: distractorScores,
            ownerConfidences: ownerScores
        )
        return SpeakerCalibrationReport(
            accepted: threshold != nil,
            threshold: threshold,
            maximumDistractorConfidence: distractorScores.max() ?? 1,
            minimumOwnerConfidence: ownerScores.min() ?? 0,
            distractorCount: distractorScores.count,
            ownerCount: ownerScores.count
        )
    }

    private func score(
        audioURL: URL,
        ownerIdentifier: String,
        expectedModelFingerprint: String?
    ) -> Double? {
        do {
            let audioFile = try AVAudioFile(forReading: audioURL)
            let format = audioFile.processingFormat
            guard
                audioFile.length > 0,
                audioFile.length <= AVAudioFramePosition(format.sampleRate * 30),
                let buffer = AVAudioPCMBuffer(
                    pcmFormat: format,
                    frameCapacity: AVAudioFrameCount(audioFile.length)
                )
            else {
                return nil
            }
            try audioFile.read(into: buffer)
            guard buffer.frameLength > 0 else { return nil }
            let session = try SpeakerIdentitySession(
                format: format,
                modelURL: modelURL,
                selectedOwnerIdentifier: ownerIdentifier,
                expectedModelFingerprint: expectedModelFingerprint,
                confidenceThreshold: 0,
                marginThreshold: 0,
                requiredObservations: 1
            )
            session.analyze(buffer)
            let result = session.finish(timeoutSeconds: 2)
            return result?.identifier == ownerIdentifier ? result?.confidence : 0
        } catch {
            return nil
        }
    }

    private func safeAudioFiles(
        in directory: URL,
        minimumCount: Int
    ) -> [URL]? {
        do {
            let directoryValues = try directory.resourceValues(
                forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
            )
            guard
                directoryValues.isDirectory == true,
                directoryValues.isSymbolicLink != true
            else {
                return nil
            }
            let candidates = try FileManager.default.contentsOfDirectory(
                at: directory,
                includingPropertiesForKeys: [
                    .isRegularFileKey,
                    .isSymbolicLinkKey,
                    .fileSizeKey,
                ],
                options: [.skipsHiddenFiles, .skipsSubdirectoryDescendants]
            ).filter { $0.pathExtension.caseInsensitiveCompare("caf") == .orderedSame }
                .sorted { $0.lastPathComponent < $1.lastPathComponent }
            let safe = try candidates.prefix(Self.maximumAudioFilesPerClass).filter { url in
                let values = try url.resourceValues(
                    forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
                )
                guard
                    values.isRegularFile == true,
                    values.isSymbolicLink != true,
                    let size = values.fileSize,
                    (68 ... Self.maximumAudioBytes).contains(size)
                else {
                    return false
                }
                let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
                let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o777
                return permissions & 0o077 == 0
            }
            return safe.count >= minimumCount ? safe : nil
        } catch {
            return nil
        }
    }
}

private enum AuthorizationAttempt: Sendable {
    case physical(Bool)
    case voice(Bool)
}

public final class DualChannelAuthorizer: @unchecked Sendable {
    public static let voiceWindowMilliseconds = 3_000
    public static let firstSemanticWindowMilliseconds = 250
    public static let semanticWindowStrideMilliseconds = 128
    public static let minimumSpeakerConfidence = 0.78

    private let modelURL: URL?
    private let distractorDirectory: URL
    private let enrollmentDirectory: URL
    private let lock = NSLock()
    private var activeProcessor: SpeechInputProcessor?
    private var activeContext: LAContext?
    private var activeVoiceCancellation: LockedFlag?
    private var calibrationFingerprint: String?
    private var calibrationThreshold: Double?
    private var calibrationAttempted = false

    public init(
        modelURL: URL? = SpeakerIdentityCapability.modelURL(),
        distractorDirectory: URL = DualChannelAuthorizer.defaultDistractorDirectory,
        enrollmentDirectory: URL = DualChannelAuthorizer.defaultEnrollmentDirectory
    ) {
        self.modelURL = modelURL
        self.distractorDirectory = distractorDirectory
        self.enrollmentDirectory = enrollmentDirectory
    }

    public static var defaultDistractorDirectory: URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return base.appending(
            path: "Aegis/Biometrics/Training/Distractors",
            directoryHint: .isDirectory
        )
    }

    public static var defaultEnrollmentDirectory: URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return base.appending(
            path: "Aegis/SpeakerEnrollment",
            directoryHint: .isDirectory
        )
    }

    public func cancel() {
        let resources = lock.withLock { () -> (SpeechInputProcessor?, LAContext?, LockedFlag?) in
            let resources = (activeProcessor, activeContext, activeVoiceCancellation)
            activeProcessor = nil
            activeContext = nil
            activeVoiceCancellation = nil
            return resources
        }
        resources.2?.set()
        resources.0?.stop()
        resources.1?.invalidate()
    }

    public func authorize(
        selectedOwnerIdentifier: String?,
        expectedModelFingerprint: String?,
        physicalApproval: @escaping @Sendable () async -> Bool,
        voiceApproval: @escaping @Sendable (VoiceAuthorizationEvidence) async -> Bool
    ) async -> DualAuthorizationResult {
        await withTaskCancellationHandler {
            await withTaskGroup(of: AuthorizationAttempt.self) { group in
                group.addTask { [weak self] in
                    guard let self else { return .physical(false) }
                    let authenticated = await authenticateOwnerPresence()
                    guard authenticated, !Task.isCancelled else { return .physical(false) }
                    return .physical(await physicalApproval())
                }
                group.addTask { [weak self] in
                    guard let self else { return .voice(false) }
                    let authorized = await authorizeByStreamingVoice(
                        selectedOwnerIdentifier: selectedOwnerIdentifier,
                        expectedModelFingerprint: expectedModelFingerprint,
                        voiceApproval: voiceApproval
                    )
                    return .voice(authorized && !Task.isCancelled)
                }
                var completedAttempts = 0
                while let attempt = await group.next() {
                    completedAttempts += 1
                    switch attempt {
                    case .physical(true):
                        group.cancelAll()
                        cancel()
                        return .authorizedByTouchID
                    case .voice(true):
                        group.cancelAll()
                        cancel()
                        return .authorizedByVoice
                    case .physical(false), .voice(false):
                        if completedAttempts == 2 {
                            cancel()
                            return .manualConfirmationRequired
                        }
                    }
                }
                cancel()
                return .manualConfirmationRequired
            }
        } onCancel: {
            cancel()
        }
    }

    private func authenticateOwnerPresence() async -> Bool {
        let context = LAContext()
        context.localizedCancelTitle = "Usar aprobación por voz"
        context.localizedFallbackTitle = "Usar botón en Jarvis"
        context.touchIDAuthenticationAllowableReuseDuration = 0
        var error: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &error
        ) else {
            return false
        }
        lock.withLock { activeContext = context }
        defer {
            lock.withLock {
                if activeContext === context { activeContext = nil }
            }
        }
        return await withCheckedContinuation { continuation in
            context.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: "Autorizar una acción protegida de Jarvis"
            ) { success, _ in
                continuation.resume(returning: success)
            }
        }
    }

    private func authorizeByStreamingVoice(
        selectedOwnerIdentifier: String?,
        expectedModelFingerprint: String?,
        voiceApproval: @escaping @Sendable (VoiceAuthorizationEvidence) async -> Bool
    ) async -> Bool {
        guard
            let selectedOwnerIdentifier,
            SpeakerIdentityCapability.isValidSpeakerLabel(selectedOwnerIdentifier),
            let modelURL
        else {
            return false
        }
        guard let preparation = await withCheckedContinuation({ continuation in
            DispatchQueue.global(qos: .utility).async { [weak self] in
                continuation.resume(
                    returning: self?.prepareStreamingVoiceCapture(
                        selectedOwnerIdentifier: selectedOwnerIdentifier,
                        expectedModelFingerprint: expectedModelFingerprint,
                        modelURL: modelURL
                    )
                )
            }
        }) else {
            return false
        }
        let processor = preparation.processor
        let speakerSession = preparation.speakerSession
        let activeThreshold = preparation.activeThreshold
        let samples = LockedPCMAccumulator(maximumSamples: 48_000)
        let failed = LockedFlag()
        let cancelled = LockedFlag()
        lock.withLock {
            activeProcessor = processor
            activeVoiceCancellation = cancelled
        }
        defer {
            processor.stop()
            lock.withLock {
                if activeProcessor === processor { activeProcessor = nil }
                if activeVoiceCancellation === cancelled { activeVoiceCancellation = nil }
            }
        }
        do {
            try processor.start(
                bufferMilliseconds: 32,
                handler: { buffer, frame in
                    speakerSession.analyze(buffer)
                    samples.append(frame.samples16k)
                },
                failureHandler: { _ in failed.set() }
            )
        } catch {
            return false
        }
        let minimumSamples = Self.firstSemanticWindowMilliseconds * 16
        let strideSamples = Self.semanticWindowStrideMilliseconds * 16
        let maximumSemanticSamples = 24_000
        let deadline = DispatchTime.now() + .milliseconds(Self.voiceWindowMilliseconds)
        var lastEvaluatedSampleCount = 0
        while
            DispatchTime.now() < deadline,
            !failed.value,
            !cancelled.value,
            !Task.isCancelled
        {
            try? await Task.sleep(for: .milliseconds(32))
            let sampleCount = samples.count
            guard
                sampleCount >= minimumSamples,
                sampleCount - lastEvaluatedSampleCount >= strideSamples,
                let identity = speakerSession.currentResult(),
                identity.identifier == selectedOwnerIdentifier,
                identity.confidence >= activeThreshold
            else {
                continue
            }
            lastEvaluatedSampleCount = sampleCount
            guard let evidence = voiceEvidence(
                samples: samples,
                maximumSamples: maximumSemanticSamples,
                identity: identity
            ) else {
                continue
            }
            if await voiceApproval(evidence) {
                return true
            }
        }
        processor.stop()
        guard
            !failed.value,
            !cancelled.value,
            !Task.isCancelled,
            let identity = speakerSession.finish(timeoutSeconds: 0.6),
            identity.identifier == selectedOwnerIdentifier,
            identity.confidence >= activeThreshold,
            samples.count != lastEvaluatedSampleCount,
            let evidence = voiceEvidence(
                samples: samples,
                maximumSamples: maximumSemanticSamples,
                identity: identity
            )
        else {
            return false
        }
        return await voiceApproval(evidence)
    }

    private func prepareStreamingVoiceCapture(
        selectedOwnerIdentifier: String,
        expectedModelFingerprint: String?,
        modelURL: URL
    ) -> StreamingVoiceCapturePreparation? {
        let processor: SpeechInputProcessor
        do {
            processor = try SpeechInputProcessor()
        } catch {
            return nil
        }
        guard
            let activeThreshold = calibratedConfidenceThreshold(
                selectedOwnerIdentifier: selectedOwnerIdentifier,
                expectedModelFingerprint: expectedModelFingerprint,
                modelURL: modelURL
            )
        else {
            return nil
        }
        guard let speakerSession = try? SpeakerIdentitySession(
            format: processor.processingFormat,
            modelURL: modelURL,
            selectedOwnerIdentifier: selectedOwnerIdentifier,
            expectedModelFingerprint: expectedModelFingerprint,
            confidenceThreshold: activeThreshold
        ) else {
            return nil
        }
        return StreamingVoiceCapturePreparation(
            processor: processor,
            speakerSession: speakerSession,
            activeThreshold: activeThreshold
        )
    }

    private func voiceEvidence(
        samples: LockedPCMAccumulator,
        maximumSamples: Int,
        identity: SpeakerIdentityResult
    ) -> VoiceAuthorizationEvidence? {
        let pcm = samples.data(suffixMaximumSamples: maximumSamples)
        guard (8_000 ... 96_000).contains(pcm.count) else { return nil }
        return VoiceAuthorizationEvidence(
            pcmS16LE: pcm,
            speakerIdentifier: identity.identifier,
            speakerConfidence: identity.confidence,
            ownerProfileMatch: true
        )
    }

    private func calibratedConfidenceThreshold(
        selectedOwnerIdentifier: String,
        expectedModelFingerprint: String?,
        modelURL: URL
    ) -> Double? {
        let fingerprint = expectedModelFingerprint
            ?? SpeakerIdentityCapability.modelFingerprint(at: modelURL)
        let cached = lock.withLock { () -> (Bool, Double?) in
            guard calibrationAttempted, calibrationFingerprint == fingerprint else {
                return (false, nil)
            }
            return (true, calibrationThreshold)
        }
        if cached.0 { return cached.1 }
        let threshold = SpeakerAdversarialCalibrator(
            modelURL: modelURL,
            distractorDirectory: distractorDirectory,
            enrollmentDirectory: enrollmentDirectory
        ).calibrate(
            ownerIdentifier: selectedOwnerIdentifier,
            expectedModelFingerprint: fingerprint
        )
        lock.withLock {
            calibrationFingerprint = fingerprint
            calibrationThreshold = threshold
            calibrationAttempted = true
        }
        return threshold
    }
}

private struct StreamingVoiceCapturePreparation: @unchecked Sendable {
    let processor: SpeechInputProcessor
    let speakerSession: SpeakerIdentitySession
    let activeThreshold: Double
}

private final class LockedFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = false
    var value: Bool { lock.withLock { stored } }
    func set() { lock.withLock { stored = true } }
}

private final class LockedPCMAccumulator: @unchecked Sendable {
    private let lock = NSLock()
    private let maximumSamples: Int
    private var bytes = Data()

    init(maximumSamples: Int) {
        self.maximumSamples = maximumSamples
        bytes.reserveCapacity(maximumSamples * MemoryLayout<Int16>.size)
    }

    func append(_ samples: [Float]) {
        lock.withLock {
            let remaining = maximumSamples - bytes.count / MemoryLayout<Int16>.size
            guard remaining > 0 else { return }
            let pcm = samples.prefix(remaining).map { sample -> Int16 in
                let clamped = min(max(sample, -1), 1)
                return Int16((clamped * Float(Int16.max)).rounded())
            }
            pcm.withUnsafeBytes { bytes.append(contentsOf: $0) }
        }
    }

    var count: Int {
        lock.withLock { bytes.count / MemoryLayout<Int16>.size }
    }

    func data(suffixMaximumSamples: Int) -> Data {
        lock.withLock {
            let maximumBytes = max(0, suffixMaximumSamples) * MemoryLayout<Int16>.size
            guard bytes.count > maximumBytes else { return bytes }
            return bytes.suffix(maximumBytes)
        }
    }
}
