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

private enum AuthorizationAttempt: Sendable {
    case physical(Bool)
    case voice(VoiceAuthorizationEvidence?)
}

public final class DualChannelAuthorizer: @unchecked Sendable {
    public static let voiceWindowMilliseconds = 3_000
    public static let minimumSpeakerConfidence = 0.78

    private let modelURL: URL?
    private let lock = NSLock()
    private var activeProcessor: SpeechInputProcessor?
    private var activeContext: LAContext?
    private var activeVoiceCancellation: LockedFlag?

    public init(modelURL: URL? = SpeakerIdentityCapability.modelURL()) {
        self.modelURL = modelURL
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
                    guard let self else { return .voice(nil) }
                    let evidence = await captureVoiceEvidence(
                        selectedOwnerIdentifier: selectedOwnerIdentifier,
                        expectedModelFingerprint: expectedModelFingerprint
                    )
                    guard let evidence, !Task.isCancelled else { return .voice(nil) }
                    return .voice(await voiceApproval(evidence) ? evidence : nil)
                }
                var completedAttempts = 0
                while let attempt = await group.next() {
                    completedAttempts += 1
                    switch attempt {
                    case .physical(true):
                        group.cancelAll()
                        cancel()
                        return .authorizedByTouchID
                    case .voice(.some):
                        group.cancelAll()
                        cancel()
                        return .authorizedByVoice
                    case .physical(false), .voice(nil):
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

    private func captureVoiceEvidence(
        selectedOwnerIdentifier: String?,
        expectedModelFingerprint: String?
    ) async -> VoiceAuthorizationEvidence? {
        guard
            let selectedOwnerIdentifier,
            SpeakerIdentityCapability.isValidSpeakerLabel(selectedOwnerIdentifier),
            let modelURL
        else {
            return nil
        }
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                guard let self else {
                    continuation.resume(returning: nil)
                    return
                }
                let result = captureVoiceEvidenceBlocking(
                    selectedOwnerIdentifier: selectedOwnerIdentifier,
                    expectedModelFingerprint: expectedModelFingerprint,
                    modelURL: modelURL
                )
                continuation.resume(returning: result)
            }
        }
    }

    private func captureVoiceEvidenceBlocking(
        selectedOwnerIdentifier: String,
        expectedModelFingerprint: String?,
        modelURL: URL
    ) -> VoiceAuthorizationEvidence? {
        let processor: SpeechInputProcessor
        do {
            processor = try SpeechInputProcessor()
        } catch {
            return nil
        }
        let format = processor.processingFormat
        guard let speakerSession = try? SpeakerIdentitySession(
            format: format,
            modelURL: modelURL,
            selectedOwnerIdentifier: selectedOwnerIdentifier,
            expectedModelFingerprint: expectedModelFingerprint
        ) else {
            return nil
        }
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
            return nil
        }
        let deadline = DispatchTime.now() + .milliseconds(Self.voiceWindowMilliseconds)
        while DispatchTime.now() < deadline, !failed.value, !cancelled.value {
            Thread.sleep(forTimeInterval: 0.02)
        }
        processor.stop()
        guard
            !failed.value,
            !cancelled.value,
            let identity = speakerSession.finish(timeoutSeconds: 0.6),
            identity.identifier == selectedOwnerIdentifier,
            identity.confidence >= Self.minimumSpeakerConfidence
        else {
            return nil
        }
        let pcm = samples.data
        guard (8_000 ... 96_000).contains(pcm.count) else { return nil }
        return VoiceAuthorizationEvidence(
            pcmS16LE: pcm,
            speakerIdentifier: identity.identifier,
            speakerConfidence: identity.confidence,
            ownerProfileMatch: true
        )
    }
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

    var data: Data { lock.withLock { bytes } }
}
