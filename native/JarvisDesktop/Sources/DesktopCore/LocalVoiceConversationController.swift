import Foundation
import Observation

public struct LocalVoiceConversationContext: Equatable, Sendable {
    public let contextID: String
    public let revision: String
    public init(contextID: String, revision: String) { self.contextID = contextID; self.revision = revision }
    fileprivate var valid: Bool {
        [contextID, revision].allSatisfy { !$0.isEmpty && $0.utf8.count <= 512 && !$0.contains("\0") }
    }
}

/// The host supplies this only after validating the durable run receipt and its
/// cleanup. Model prose alone is not a successful or settled conversation turn.
public struct LocalVoiceConversationReply: Equatable, Sendable {
    public let runID: String
    public let status: String
    public let outcome: String
    public let answer: String
    public let approvalRequired: Bool
    public let unconfirmedEffects: Bool
    public init(runID: String, status: String, outcome: String, answer: String,
                approvalRequired: Bool = false, unconfirmedEffects: Bool = false) {
        self.runID = runID; self.status = status; self.outcome = outcome; self.answer = answer
        self.approvalRequired = approvalRequired; self.unconfirmedEffects = unconfirmedEffects
    }
    fileprivate var maySpeak: Bool {
        ManagementValidation.identifier(runID) && status == "completed" && outcome == "known"
            && !approvalRequired && !unconfirmedEffects && !answer.isEmpty && answer.utf8.count <= 16000 && !answer.contains("\0")
    }
}

public enum LocalVoiceConversationPhase: String, Equatable, Sendable { case idle, listening, waitingForModel, speaking, stopping }
public enum LocalVoiceConversationStop: String, Equatable, Sendable {
    case ownerStopped, contextChanged, approvalRequired, partialTranscript, captureFailed
    case runUnconfirmed, playbackFailed, turnLimit, timeLimit, cleanupPending
    case permissionRevoked, interrupted, possibleEcho
    public var message: String {
        switch self {
        case .ownerStopped: "Conversación por voz detenida."
        case .contextChanged: "La conversación o su autorización cambió. Vuelve a revisar el modo de voz antes de iniciarlo."
        case .approvalRequired: "La herramienta necesita una decisión. La conversación por voz se detuvo; revisa la operación en el chat."
        case .partialTranscript: "El reconocimiento quedó parcial. Revisa el texto en lugar de enviarlo automáticamente."
        case .captureFailed: "La captura terminó sin una frase final válida. La conversación por voz se detuvo."
        case .runUnconfirmed: "La ejecución no terminó con un recibo confirmado. No se repetirá por voz."
        case .playbackFailed: "La lectura local no terminó correctamente. El micrófono permanece cerrado."
        case .turnLimit: "Terminó el límite de tres turnos. Puedes revisar e iniciar otra conversación por voz."
        case .timeLimit: "Terminó el límite de cinco minutos. La conversación por voz se detuvo."
        case .cleanupPending: "La limpieza de la ejecución sigue pendiente. No se iniciará otra captura."
        case .permissionRevoked: "Cambió el permiso de voz. La conversación se detuvo y el micrófono permanece cerrado."
        case .interrupted: "La aplicación perdió el foco o el Mac se suspendió. La conversación por voz se detuvo."
        case .possibleEcho: "El texto coincide con la respuesta que se acaba de leer. Revisa el posible eco antes de enviarlo."
        }
    }
}

/// Explicitly reviewed Talk mode, distinct from draft-only dictation. At most
/// three finalized utterances become TEXT requests through the normal host run
/// path. Audio never reaches the model; tool authority is never broadened.
/// The microphone is closed during model work and local playback.
@MainActor @Observable public final class LocalVoiceConversationController {
    public private(set) var phase: LocalVoiceConversationPhase = .idle
    public private(set) var completedTurns = 0
    public private(set) var stopReason: LocalVoiceConversationStop?
    public private(set) var activeContext: LocalVoiceConversationContext?
    public private(set) var lastRunID: String?
    public private(set) var partialForReview: LocalVoiceTranscript?
    public let maximumTurns = 3
    public let maximumSeconds: TimeInterval = 300

    @ObservationIgnored private let voice: LocalVoiceController
    @ObservationIgnored private let clock: any LocalVoiceClock
    @ObservationIgnored private let currentContext: @MainActor () -> LocalVoiceConversationContext?
    @ObservationIgnored private let submit: @MainActor (String, LocalVoiceConversationContext) async throws -> LocalVoiceConversationReply
    @ObservationIgnored private let cancelRun: @MainActor (LocalVoiceConversationContext) async -> Bool
    @ObservationIgnored private var generation: UInt64 = 0
    @ObservationIgnored private var work: Task<Void, Never>?
    @ObservationIgnored private var cleanup: Task<Void, Never>?
    @ObservationIgnored private var retiringWork: Task<Void, Never>?
    @ObservationIgnored private var deadline: (any LocalVoiceCancellation)?
    @ObservationIgnored private var settings = LocalVoicePreferences()
    @ObservationIgnored private var captureID: UUID?
    @ObservationIgnored private var playbackID: UUID?
    @ObservationIgnored private var retiredCaptureIDs = Set<UUID>()
    @ObservationIgnored private var completedRunIDs = Set<String>()
    @ObservationIgnored private var lastSpokenText: String?
    @ObservationIgnored private var rearmDeadline: (any LocalVoiceCancellation)?
    @ObservationIgnored private var retainedContext: LocalVoiceConversationContext?

    public init(voice: LocalVoiceController, clock: (any LocalVoiceClock)? = nil,
                currentContext: @escaping @MainActor () -> LocalVoiceConversationContext?,
                submit: @escaping @MainActor (String, LocalVoiceConversationContext) async throws -> LocalVoiceConversationReply,
                cancelRun: @escaping @MainActor (LocalVoiceConversationContext) async -> Bool) {
        self.voice = voice; self.clock = clock ?? SystemLocalVoiceClock()
        self.currentContext = currentContext; self.submit = submit; self.cancelRun = cancelRun
    }

    public var isActive: Bool { phase != .idle }
    public var statusText: String {
        switch phase {
        case .idle: return stopReason?.message ?? "Conversar por voz requiere una revisión explícita."
        case .listening:
            return voice.phase == .authorizing
                ? "Conversación por voz · Preparando captura local y permisos…"
                : "Conversación por voz · Captura local · Turno \(completedTurns + 1) de 3"
        case .waitingForModel: return "Micrófono cerrado · Codex está procesando el texto reconocido"
        case .speaking: return voice.phase == .speaking ? "Micrófono cerrado · Lectura local de la respuesta" : "Micrófono cerrado · Pausa después de la lectura"
        case .stopping: return "Cerrando conversación por voz y esperando su recibo de limpieza…"
        }
    }

    /// The native owner review must immediately precede this call and bind this
    /// exact context/settings. No RPC or model tool calls this entry point.
    @discardableResult public func start(context: LocalVoiceConversationContext) -> Bool {
        guard phase == .idle, context.valid, currentContext() == context,
              voice.canStartDictation, LocalSpeechPolicy.canonicalLocale(voice.preferences.localeIdentifier) != nil else { return false }
        let available = voice.availableVoices(localeIdentifier: voice.preferences.localeIdentifier)
        guard voice.preferences.voiceID.map({ id in available.contains { $0.id == id } }) ?? !available.isEmpty else {
            stopReason = .playbackFailed; return false
        }
        voice.cancel()
        generation &+= 1; let ticket = generation
        settings = voice.preferences; activeContext = context; retainedContext = context; completedTurns = 0
        lastRunID = nil; stopReason = nil; partialForReview = nil
        completedRunIDs = []
        lastSpokenText = nil
        phase = .listening
        deadline = clock.schedule(after: maximumSeconds) { [weak self] in
            guard let self, self.generation == ticket else { return }
            self.stop(reason: .timeLimit)
        }
        startCapture(ticket)
        return phase != .idle
    }

    /// Route transcript callbacks here before the ordinary draft bridge. A late
    /// Talk transcript must be discarded rather than becoming another draft.
    @discardableResult public func acceptTranscript(_ value: LocalVoiceTranscript) -> Bool {
        guard let context = activeContext, value.contextID == context.contextID else {
            return value.captureID.map(retiredCaptureIDs.contains) ?? false
        }
        guard value.captureID == captureID, captureID != nil else { return true }
        guard matches(context) else { stop(reason: .contextChanged); return true }
        guard phase == .listening else { return true }
        guard value.source == "on_device_speech", value.requiresReview,
              value.completion == .recognizedFinal,
              !value.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              value.text.utf8.count <= 16000, !value.text.contains("\0") else {
            partialForReview = value; stop(reason: .partialTranscript); return true
        }
        if let lastSpokenText, Self.isPossibleEcho(value.text, spoken: lastSpokenText) {
            partialForReview = value; stop(reason: .possibleEcho); return true
        }
        phase = .waitingForModel
        let ticket = generation, text = value.text
        let submit = submit
        // The controller has stopped input before delivering this callback.
        work = Task { @MainActor [weak self] in
            guard self?.generation == ticket, self?.matches(context) == true else { return }
            do {
                let reply = try await submit(text, context)
                guard let self, self.generation == ticket, self.phase == .waitingForModel else { return }
                self.work = nil
                guard self.matches(context) else { self.stop(reason: .contextChanged); return }
                guard reply.maySpeak, !self.completedRunIDs.contains(reply.runID) else {
                    self.stop(reason: reply.approvalRequired ? .approvalRequired : .runUnconfirmed); return
                }
                self.completedRunIDs.insert(reply.runID)
                self.lastRunID = reply.runID; self.completedTurns += 1; self.phase = .speaking
                self.lastSpokenText = reply.answer
                let playbackID = UUID(); self.playbackID = playbackID
                self.voice.play(text: reply.answer, contextID: context.contextID,
                                localeIdentifier: self.settings.localeIdentifier, voiceID: self.settings.voiceID, playbackID: playbackID)
                if self.phase == .speaking, self.playbackID != nil, self.voice.phase != .speaking { self.stop(reason: .playbackFailed) }
            } catch {
                guard let self, self.generation == ticket else { return }
                self.work = nil; self.stop(reason: .runUnconfirmed)
            }
        }
        return true
    }

    public func captureCompleted(_ receipt: LocalVoiceCaptureReceipt) {
        guard let context = activeContext, receipt.contextID == context.contextID,
              receipt.captureID == captureID, captureID != nil, phase == .listening else { return }
        if receipt.outcome != .transcript { stop(reason: .captureFailed) }
    }

    public func playbackCompleted(_ receipt: LocalVoicePlaybackReceipt) {
        guard let context = activeContext, receipt.contextID == context.contextID,
              receipt.playbackID == playbackID, playbackID != nil, phase == .speaking else { return }
        guard matches(context) else { stop(reason: .contextChanged); return }
        guard receipt.event == .finished, receipt.issue == nil else { stop(reason: .playbackFailed); return }
        playbackID = nil
        guard completedTurns < maximumTurns else { stop(reason: .turnLimit); return }
        let ticket = generation
        rearmDeadline = clock.schedule(after: 0.35) { [weak self] in
            guard let self, self.generation == ticket, self.phase == .speaking, self.playbackID == nil else { return }
            self.rearmDeadline = nil
            guard self.matches(context) else { self.stop(reason: .contextChanged); return }
            guard self.voice.phase == .idle else { self.stop(reason: .playbackFailed); return }
            self.phase = .listening; self.startCapture(ticket)
        }
    }

    public func validateContext() {
        guard let context = activeContext else { return }
        if !matches(context) { partialForReview = nil; stop(reason: .contextChanged) }
    }

    public func approvalBecamePending() {
        if isActive { stop(reason: .approvalRequired) }
    }

    public func stop(reason: LocalVoiceConversationStop = .ownerStopped) {
        if reason == .contextChanged || reason == .ownerStopped { partialForReview = nil }
        guard phase != .idle else { return }
        if phase != .stopping {
            generation &+= 1; phase = .stopping; stopReason = reason
            retireCapture()
            deadline?.cancel(); deadline = nil
            rearmDeadline?.cancel(); rearmDeadline = nil
            retiringWork = work; work = nil; retiringWork?.cancel()
            voice.cancel()
        }
        retryCleanup()
    }

    /// A separate visible owner action may preserve a partial in its original
    /// draft. It can never be consumed from a different or still-cleaning scope.
    public func takePartialForReview(context: LocalVoiceConversationContext) -> LocalVoiceTranscript? {
        guard phase == .idle, matches(context), let value = partialForReview,
              value.contextID == context.contextID else { return nil }
        partialForReview = nil; return value
    }

    public func retryCleanup() {
        guard phase == .stopping, cleanup == nil, let context = activeContext else { return }
        let ticket = generation, retiring = retiringWork
        let cancelRun = cancelRun
        cleanup = Task { @MainActor [weak self] in
            let confirmed = await cancelRun(context)
            guard let self, self.generation == ticket, self.phase == .stopping else { return }
            guard confirmed else {
                self.cleanup = nil; self.stopReason = .cleanupPending; return
            }
            await retiring?.value
            guard self.generation == ticket, self.phase == .stopping else { return }
            self.cleanup = nil
            self.retiringWork = nil; self.activeContext = nil; self.retainedContext = nil; self.phase = .idle
        }
    }

    private func matches(_ context: LocalVoiceConversationContext) -> Bool { currentContext() == context }

    private static func isPossibleEcho(_ input: String, spoken: String) -> Bool {
        func words(_ value: String) -> String {
            value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "en_US_POSIX"))
                .components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }.joined(separator: " ")
        }
        let candidate = words(input), previous = words(spoken)
        guard !candidate.isEmpty else { return false }
        return candidate == previous || (candidate.utf8.count >= 16 && candidate.split(separator: " ").count >= 3 && previous.hasSuffix(" " + candidate))
    }
    private func startCapture(_ ticket: UInt64) {
        guard generation == ticket, phase == .listening, let context = activeContext, matches(context) else {
            stop(reason: .contextChanged); return
        }
        retireCapture()
        let captureID = UUID(); self.captureID = captureID; playbackID = nil
        voice.startDictation(contextID: context.contextID, localeIdentifier: settings.localeIdentifier,
                             captureID: captureID, endpointAfterSilence: 1.5)
        if phase == .listening, voice.phase != .listening && voice.phase != .authorizing { stop(reason: .captureFailed) }
    }

    private func retireCapture() {
        guard let captureID else { return }
        if retiredCaptureIDs.count >= 32 { retiredCaptureIDs.removeAll(keepingCapacity: true) }
        retiredCaptureIDs.insert(captureID)
    }

    deinit {
        let voice = voice, work = work, cleanup = cleanup, retiring = retiringWork
        let context = retainedContext, cancelRun = cancelRun, deadline = deadline, rearmDeadline = rearmDeadline
        Task { @MainActor in
            deadline?.cancel(); rearmDeadline?.cancel(); work?.cancel(); voice.cancel()
            if let cleanup { await cleanup.value }
            else if let context { _ = await cancelRun(context) }
            await work?.value; await retiring?.value
        }
    }
}
