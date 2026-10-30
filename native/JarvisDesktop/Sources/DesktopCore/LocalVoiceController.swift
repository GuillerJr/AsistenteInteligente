import Foundation
import Observation

public enum LocalVoicePhase: String, Equatable, Sendable {
    case idle, authorizing, listening, finishing, speaking
}

public enum LocalVoiceIssue: Error, Equatable, Sendable {
    case microphoneDenied, speechDenied, onDeviceUnavailable, captureFailed
    case recognitionFailed, noLocalVoice, playbackFailed, invalidContext, textLimit
    case authorizationTimeout, playbackTimeout, busy, configuration
    case invalidSettings, wakePhraseNotHeard, interrupted, inputChanged, cleanupUnconfirmed

    public var message: String {
        switch self {
        case .microphoneDenied: "El micrófono no está autorizado. Revisa los permisos de Jarvis en Ajustes del Sistema."
        case .speechDenied: "El reconocimiento de voz no está autorizado. Revisa los permisos de Jarvis en Ajustes del Sistema."
        case .onDeviceUnavailable: "El reconocimiento local no está disponible para este idioma en este Mac. No se enviará audio a otro servicio."
        case .captureFailed: "No se pudo iniciar el micrófono. Comprueba el dispositivo de entrada e inténtalo de nuevo."
        case .recognitionFailed: "El dictado local terminó con un error. No se envió ningún mensaje."
        case .noLocalVoice: "No hay una voz local instalada disponible para leer este texto en el idioma elegido."
        case .playbackFailed: "No se pudo iniciar la lectura local."
        case .invalidContext: "Selecciona una conversación antes de usar la voz."
        case .textLimit: "El texto supera el límite de la función de voz. Usa un fragmento más corto."
        case .authorizationTimeout: "Se agotó la preparación del dictado o la espera de permisos. Puedes intentarlo de nuevo."
        case .playbackTimeout: "Se detuvo la lectura al alcanzar su límite de tiempo."
        case .busy: "Termina el dictado actual antes de iniciar otra operación de voz."
        case .configuration: "El paquete de Jarvis no incluye la configuración de permisos de voz. Revisa su instalación."
        case .invalidSettings: "Revisa el idioma, la voz local y la frase de activación antes de iniciar."
        case .wakePhraseNotHeard: "No se detectó la frase de activación. No se añadió texto al borrador."
        case .interrupted: "La voz local se interrumpió. El micrófono se cerró; vuelve a iniciarlo cuando quieras."
        case .inputChanged: "Cambió el dispositivo o el formato de entrada. El dictado se detuvo sin reiniciarse."
        case .cleanupUnconfirmed: "El reconocimiento anterior aún está cerrándose. El micrófono permanece detenido; vuelve a intentarlo después."
        }
    }
}

public enum LocalVoicePermission: Equatable, Sendable {
    case authorized, denied, restricted, notDetermined
}

public struct LocalVoicePermissions: Equatable, Sendable {
    public let speech: LocalVoicePermission
    public let microphone: LocalVoicePermission
    public let configurationMissing: Bool
    public init(speech: LocalVoicePermission, microphone: LocalVoicePermission, configurationMissing: Bool = false) {
        self.speech = speech; self.microphone = microphone
        self.configurationMissing = configurationMissing
    }
}

public struct LocalVoiceChoice: Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let language: String
    public init(id: String, name: String, language: String) {
        self.id = id; self.name = name; self.language = language
    }
}

/// Only known Apple system voice namespaces can reach the local SDK adapter.
/// The adapter additionally requires a downloaded identifier and excludes Personal Voice.
public enum MacLocalVoicePolicy {
    public static func isAllowed(identifier: String, isPersonalVoice: Bool) -> Bool {
        !isPersonalVoice && identifier.utf8.count <= 256
        && identifier.unicodeScalars.allSatisfy {
            (65...90).contains($0.value) || (97...122).contains($0.value)
            || (48...57).contains($0.value) || $0 == "." || $0 == "-" || $0 == "_"
        }
        && (identifier.hasPrefix("com.apple.voice.") || identifier.hasPrefix("com.apple.ttsbundle."))
    }
}

public enum LocalRecognitionEvent: Equatable, Sendable {
    case partial(String), final(String), failed, issue(LocalVoiceIssue)
}

/// Locale identity is exact apart from separator/case normalization. A different
/// regional model is never a replacement for the language selected by the owner.
public enum LocalSpeechPolicy {
    public static func canonicalLocale(_ value: String) -> String? {
        guard !value.isEmpty, value.utf8.count <= 64,
              value.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" }) else { return nil }
        let parts = value.replacingOccurrences(of: "_", with: "-").split(separator: "-", omittingEmptySubsequences: false)
        guard parts.allSatisfy({ !$0.isEmpty }), (2...8).contains(parts[0].count) else { return nil }
        return parts.map { $0.lowercased() }.joined(separator: "-")
    }

    public static func hasExactInstalledLocale(_ requested: String, supported: [String], installed: [String]) -> Bool {
        guard let target = canonicalLocale(requested) else { return false }
        return supported.contains { canonicalLocale($0) == target }
            && installed.contains { canonicalLocale($0) == target }
    }

    public static let queuedAudioBuffers = 8
    public static let maximumPCMBufferBytes = 65_536
    public static func acceptsPCM(sampleRate: Double, channels: UInt32, frames: UInt32, bytes: Int) -> Bool {
        sampleRate.isFinite && sampleRate > 0 && sampleRate <= 96_000
            && (1...2).contains(channels) && frames > 0 && frames <= 8192
            && bytes > 0 && bytes <= maximumPCMBufferBytes
    }
}

/// SpeechTranscriber finalizes segments while the input session remains open.
/// This accumulator replaces volatile hypotheses and retains each final range
/// once; overlap/contradiction is an error rather than duplicated draft material.
public struct LocalSpeechSegments: Sendable {
    private struct Segment: Sendable { let start: Double; let end: Double; let text: String }
    private var finalized: [Segment] = []
    private var volatile: Segment?
    private let maximumBytes: Int
    public init(maximumBytes: Int = 16000) { self.maximumBytes = maximumBytes }

    public var text: String { Self.join(finalized.map(\.text) + (volatile.map { [$0.text] } ?? [])) }
    public var finalizedText: String { Self.join(finalized.map(\.text)) }
    public var hasUnfinalizedText: Bool { volatile.map { !$0.text.isEmpty } ?? false }

    public mutating func accept(text: String, start: Double, end: Double, isFinal: Bool) throws -> String {
        guard start.isFinite, end.isFinite, start >= 0, end >= start else { throw LocalVoiceIssue.recognitionFailed }
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.utf8.count <= maximumBytes else { throw LocalVoiceIssue.textLimit }
        if let old = finalized.first(where: { $0.start == start && $0.end == end }) {
            guard old.text == value else { throw LocalVoiceIssue.recognitionFailed }
            return self.text
        }
        guard finalized.last.map({ start >= $0.end }) ?? true,
              finalized.count < 512 else { throw LocalVoiceIssue.recognitionFailed }
        var candidate = self
        let segment = Segment(start: start, end: end, text: value)
        if isFinal {
            candidate.finalized.append(segment)
            // A final may cover the earlier volatile range. A later hypothesis
            // remains relevant only when wholly after this finalized segment.
            if candidate.volatile.map({ $0.start < end || ($0.start == start && $0.end == end) }) ?? false { candidate.volatile = nil }
        } else {
            candidate.volatile = segment
        }
        guard candidate.text.utf8.count <= maximumBytes else { throw LocalVoiceIssue.textLimit }
        self = candidate
        return self.text
    }

    private static func join(_ values: [String]) -> String {
        values.filter { !$0.isEmpty }.reduce("") { result, value in
            guard !result.isEmpty else { return value }
            let punctuation = value.first.map { ".,;:!?…)]}".contains($0) } ?? false
            return result + (punctuation ? "" : " ") + value
        }
    }
}

public enum LocalPlaybackEvent: Equatable, Sendable {
    case finished, cancelled, failed
}

public struct LocalVoicePlaybackReceipt: Equatable, Sendable {
    public let contextID: String
    public let playbackID: UUID?
    public let event: LocalPlaybackEvent
    public let issue: LocalVoiceIssue?
    public init(contextID: String, playbackID: UUID? = nil, event: LocalPlaybackEvent, issue: LocalVoiceIssue? = nil) {
        self.contextID = contextID; self.playbackID = playbackID; self.event = event; self.issue = issue
    }
}

public struct LocalVoiceCaptureReceipt: Equatable, Sendable {
    public enum Outcome: Equatable, Sendable { case transcript, empty, cancelled, failed }
    public let contextID: String
    public let captureID: UUID?
    public let outcome: Outcome
    public let issue: LocalVoiceIssue?
    public let captureMode: LocalVoiceCaptureMode
    public let wakeMatched: Bool
    public let wakeProjection: LocalWakePhraseProjectionMetadata?
    public init(contextID: String, captureID: UUID? = nil, outcome: Outcome, issue: LocalVoiceIssue? = nil,
                captureMode: LocalVoiceCaptureMode = .dictation, wakeMatched: Bool = false,
                wakeProjection: LocalWakePhraseProjectionMetadata? = nil) {
        self.contextID = contextID; self.captureID = captureID; self.outcome = outcome; self.issue = issue
        self.captureMode = captureMode; self.wakeMatched = wakeMatched; self.wakeProjection = wakeProjection
    }
}

public struct LocalVoiceTranscript: Equatable, Sendable {
    public enum Completion: Equatable, Sendable { case recognizedFinal, partialAtStop }
    public let contextID: String
    public let text: String
    public let completion: Completion
    public let captureID: UUID?
    public let captureMode: LocalVoiceCaptureMode
    public let wakeMatched: Bool
    public let source = "on_device_speech"
    public let requiresReview = true
    public init(contextID: String, text: String, completion: Completion, captureID: UUID? = nil,
                captureMode: LocalVoiceCaptureMode = .dictation, wakeMatched: Bool = false) {
        self.contextID = contextID; self.text = text; self.completion = completion; self.captureID = captureID
        self.captureMode = captureMode; self.wakeMatched = wakeMatched
    }
}

@MainActor public protocol LocalVoiceCancellation: AnyObject {
    func cancel()
}

@MainActor public protocol LocalVoiceClock: AnyObject {
    func schedule(after seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any LocalVoiceCancellation
}

@MainActor public protocol LocalVoiceDriver: AnyObject {
    /// Invoked only by the owner's explicit dictation action, never at initialization.
    func requestPermissions(_ completion: @escaping @MainActor (LocalVoicePermissions) -> Void) -> any LocalVoiceCancellation
    /// No input hardware is opened until this explicit, cancellable preparation
    /// succeeds. It must never download assets or silently select another locale.
    func prepareRecognition(localeIdentifier: String, completion: @escaping @MainActor (Result<Void, LocalVoiceIssue>) -> Void) -> any LocalVoiceCancellation
    func beginRecognition(localeIdentifier: String, receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws
    func finishRecognition()
    func cancelRecognition()
    func localVoices(localeIdentifier: String) -> [LocalVoiceChoice]
    func beginPlayback(text: String, voice: LocalVoiceChoice, receive: @escaping @MainActor (LocalPlaybackEvent) -> Void) throws
    func stopPlayback()
}

@MainActor private final class VoiceDeadline: LocalVoiceCancellation {
    private let task: Task<Void, Never>
    init(seconds: TimeInterval, action: @escaping @MainActor () -> Void) {
        task = Task {
            do { try await Task.sleep(for: .seconds(seconds)) } catch { return }
            guard !Task.isCancelled else { return }
            action()
        }
    }
    func cancel() { task.cancel() }
}

@MainActor public final class SystemLocalVoiceClock: LocalVoiceClock {
    public init() {}
    public func schedule(after seconds: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any LocalVoiceCancellation {
        VoiceDeadline(seconds: seconds, action: action)
    }
}

/// The core owns intent and bounded lifecycle; the SDK adapter owns audio resources.
/// A transcript is offered to its originating draft, never submitted to the model.
@MainActor @Observable public final class LocalVoiceController {
    public private(set) var phase: LocalVoicePhase = .idle
    public private(set) var issue: LocalVoiceIssue?
    public private(set) var partialText = ""
    public private(set) var contextID: String?
    public private(set) var activeVoiceName: String?
    public var preferences = LocalVoicePreferences()
    public private(set) var activeCaptureMode: LocalVoiceCaptureMode?
    public private(set) var wakePhraseDetected = false
    public private(set) var activeCaptureSeconds: TimeInterval = 30
    public private(set) var captureIdentifier: UUID?
    /// The last settled capture has metadata only, retained in RAM. Ambient
    /// hypotheses and transcripts are never added to this diagnostic receipt.
    public private(set) var lastCaptureReceipt: LocalVoiceCaptureReceipt?
    public private(set) var playbackIdentifier: UUID?
    @ObservationIgnored public var onTranscript: (@MainActor (LocalVoiceTranscript) -> Void)?
    @ObservationIgnored public var onPlaybackCompletion: (@MainActor (LocalVoicePlaybackReceipt) -> Void)?
    @ObservationIgnored public var onCaptureCompletion: (@MainActor (LocalVoiceCaptureReceipt) -> Void)?

    @ObservationIgnored private let driver: any LocalVoiceDriver
    @ObservationIgnored private let clock: any LocalVoiceClock
    @ObservationIgnored private let captureSeconds: TimeInterval
    @ObservationIgnored private let playbackSeconds: TimeInterval
    @ObservationIgnored private let maxTextBytes: Int
    @ObservationIgnored private var generation: UInt64 = 0
    @ObservationIgnored private var permission: (any LocalVoiceCancellation)?
    @ObservationIgnored private var preparation: (any LocalVoiceCancellation)?
    @ObservationIgnored private var deadline: (any LocalVoiceCancellation)?
    @ObservationIgnored private var selectedWakePhrase: String?
    @ObservationIgnored private var silenceDeadline: (any LocalVoiceCancellation)?
    @ObservationIgnored private var endpointAfterSilence: TimeInterval?
    @ObservationIgnored private var wakeProjection: LocalWakePhraseProjectionMetadata?
    @ObservationIgnored private var silenceGeneration: UInt64 = 0

    public init(driver: any LocalVoiceDriver, clock: (any LocalVoiceClock)? = nil,
                captureSeconds: TimeInterval = 30, playbackSeconds: TimeInterval = 120,
                maxTextBytes: Int = 16000) {
        precondition(captureSeconds.isFinite && (0.05...30).contains(captureSeconds))
        precondition(playbackSeconds.isFinite && (0.05...120).contains(playbackSeconds))
        precondition((1...16000).contains(maxTextBytes))
        self.driver = driver; self.clock = clock ?? SystemLocalVoiceClock()
        self.captureSeconds = captureSeconds; self.playbackSeconds = playbackSeconds
        self.maxTextBytes = maxTextBytes
        // No driver, permission, hardware, recognition or voice query here.
    }

    deinit {
        // Views still cancel synchronously on disappearance/session change.
        // This fallback retains the adapter until its SDK resources are released.
        let driver = driver, permission = permission, preparation = preparation, deadline = deadline, silenceDeadline = silenceDeadline
        Task { @MainActor in
            permission?.cancel(); preparation?.cancel(); deadline?.cancel(); silenceDeadline?.cancel()
            driver.cancelRecognition(); driver.stopPlayback()
        }
    }

    public var isCapturing: Bool { phase == .listening }
    public var canStartDictation: Bool { phase == .idle || phase == .speaking }
    public var canStopDictation: Bool { phase == .authorizing || phase == .listening || phase == .finishing }
    public var statusText: String {
        if let issue { return issue.message }
        switch phase {
        case .idle: return "Voz local · El dictado se añade al borrador para que lo revises."
        case .authorizing: return "Preparando el dictado local y esperando permisos…"
        case .listening:
            if activeCaptureMode == .wakePhrase && !wakePhraseDetected {
                return "Escucha local activada por ti · Esperando la frase de activación · Máximo \(Int(activeCaptureSeconds)) segundos"
            }
            return "Dictando en este Mac · Máximo \(Int(activeCaptureSeconds)) segundos · Revisa el borrador antes de enviar"
        case .finishing: return "Finalizando el dictado local…"
        case .speaking: return "Leyendo con una voz local…"
        }
    }

    public func startSelectedDictation(contextID: String) {
        let settings = preferences
        startDictation(contextID: contextID, localeIdentifier: settings.localeIdentifier,
                       mode: settings.mode, wakePhrase: settings.mode == .wakePhrase ? settings.wakePhrase : nil)
    }

    public func startDictation(contextID: String, localeIdentifier: String = "es-ES",
                               mode: LocalVoiceCaptureMode = .dictation, wakePhrase: String? = nil, captureID: UUID = UUID(),
                               endpointAfterSilence: TimeInterval? = nil) {
        guard canStartDictation else { issue = .busy; return }
        guard validContext(contextID) else { issue = .invalidContext; return }
        guard LocalSpeechPolicy.canonicalLocale(localeIdentifier) != nil,
              mode != .wakePhrase || wakePhrase.map(LocalWakePhrasePolicy.isValid) == true,
              endpointAfterSilence.map({ $0.isFinite && (0.5...3).contains($0) }) ?? true else {
            issue = .invalidSettings; return
        }
        invalidate()
        driver.stopPlayback()
        issue = nil; partialText = ""; activeVoiceName = nil
        activeCaptureMode = mode; selectedWakePhrase = wakePhrase; wakePhraseDetected = false
        lastCaptureReceipt = nil; wakeProjection = nil
        self.endpointAfterSilence = endpointAfterSilence
        captureIdentifier = captureID; playbackIdentifier = nil
        activeCaptureSeconds = captureSeconds * mode.durationMultiplier
        self.contextID = contextID; phase = .authorizing
        let token = generation
        deadline = clock.schedule(after: 30) { [weak self] in
            guard let self, self.generation == token, self.phase == .authorizing else { return }
            self.fail(.authorizationTimeout)
        }
        var resolvedPermission = false
        let request = driver.requestPermissions { [weak self] permissions in
            guard let self, self.generation == token, self.phase == .authorizing, !resolvedPermission else { return }
            resolvedPermission = true
            self.permission?.cancel(); self.permission = nil
            guard !permissions.configurationMissing else { self.fail(.configuration); return }
            guard permissions.speech == .authorized else { self.fail(.speechDenied); return }
            guard permissions.microphone == .authorized else { self.fail(.microphoneDenied); return }
            let preparing = self.driver.prepareRecognition(localeIdentifier: localeIdentifier) { [weak self] result in
                guard let self, self.generation == token, self.phase == .authorizing else { return }
                self.preparation?.cancel(); self.preparation = nil
                switch result {
                case let .failure(issue): self.fail(issue)
                case .success:
                    self.deadline?.cancel(); self.deadline = nil
                    self.phase = .listening
                    do {
                        try self.driver.beginRecognition(localeIdentifier: localeIdentifier) { [weak self] event in
                            self?.recognition(event, generation: token)
                        }
                        guard self.generation == token, self.phase == .listening else { return }
                        self.deadline = self.clock.schedule(after: self.activeCaptureSeconds) { [weak self] in
                            guard let self, self.generation == token, self.phase == .listening else { return }
                            self.stopDictation()
                        }
                    } catch { self.fail((error as? LocalVoiceIssue) ?? .captureFailed) }
                }
            }
            if self.generation == token, self.phase == .authorizing { self.preparation = preparing }
            else { preparing.cancel() }
        }
        if phase == .authorizing, generation == token { permission = request }
        else { request.cancel() }
    }

    public func stopDictation() {
        if phase == .authorizing || phase == .finishing { cancel(); return }
        guard phase == .listening else { return }
        phase = .finishing
        deadline?.cancel(); deadline = nil
        silenceDeadline?.cancel(); silenceDeadline = nil
        silenceGeneration &+= 1
        let token = generation
        driver.finishRecognition()
        guard generation == token, phase == .finishing else { return }
        deadline = clock.schedule(after: 3) { [weak self] in
            guard let self, self.generation == token, self.phase == .finishing else { return }
            self.publish(completion: .partialAtStop)
        }
    }

    public func play(text: String, contextID: String, localeIdentifier: String = "es-ES", voiceID: String? = nil, playbackID: UUID = UUID()) {
        guard phase == .idle || phase == .speaking else { issue = .busy; return }
        guard validContext(contextID) else { issue = .invalidContext; return }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= maxTextBytes else { issue = .textLimit; return }
        let voices = availableVoices(localeIdentifier: localeIdentifier)
        let voice = voiceID.flatMap { id in voices.first { $0.id == id } } ?? (voiceID == nil ? voices.first : nil)
        guard let voice else { issue = .noLocalVoice; return }
        invalidate(); driver.stopPlayback()
        phase = .speaking; issue = nil; self.contextID = contextID; activeVoiceName = voice.name
        playbackIdentifier = playbackID; captureIdentifier = nil
        let token = generation
        do {
            try driver.beginPlayback(text: text, voice: voice) { [weak self] event in
                guard let self, self.generation == token, self.phase == .speaking else { return }
                self.endPlayback(event: event, issue: event == .failed ? .playbackFailed : nil)
            }
            guard generation == token, phase == .speaking else { return }
            deadline = clock.schedule(after: playbackSeconds) { [weak self] in
                guard let self, self.generation == token, self.phase == .speaking else { return }
                self.endPlayback(event: .failed, issue: .playbackTimeout)
            }
        } catch { endPlayback(event: .failed, issue: (error as? LocalVoiceIssue) ?? .playbackFailed) }
    }

    public func playSelected(text: String, contextID: String) {
        play(text: text, contextID: contextID, localeIdentifier: preferences.localeIdentifier, voiceID: preferences.voiceID)
    }

    /// Called from the explicit voice-settings surface; it never requests TCC.
    public func availableVoices(localeIdentifier: String) -> [LocalVoiceChoice] {
        guard let requested = LocalSpeechPolicy.canonicalLocale(localeIdentifier)?.split(separator: "-").first else { return [] }
        var seen = Set<String>()
        return Array(driver.localVoices(localeIdentifier: localeIdentifier).filter { value in
            MacLocalVoicePolicy.isAllowed(identifier: value.id, isPersonalVoice: false)
                && LocalSpeechPolicy.canonicalLocale(value.language)?.split(separator: "-").first == requested
                && !value.name.isEmpty && value.name.utf8.count <= 256
                && !value.name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
                && seen.insert(value.id).inserted
        }.prefix(128))
    }

    /// The view calls this on conversation change, window close, logout and shutdown.
    public func cancel() {
        let playbackContext = phase == .speaking ? contextID : nil
        let captureContext = canStopDictation ? contextID : nil
        let oldPlayback = playbackIdentifier, oldCapture = captureIdentifier
        let mode = activeCaptureMode ?? .dictation, matched = wakePhraseDetected, projection = wakeProjection
        reset()
        if let contextID = playbackContext {
            onPlaybackCompletion?(.init(contextID: contextID, playbackID: oldPlayback, event: .cancelled, issue: nil))
        }
        if let contextID = captureContext {
            let receipt = LocalVoiceCaptureReceipt(contextID: contextID, captureID: oldCapture, outcome: .cancelled,
                captureMode: mode, wakeMatched: matched, wakeProjection: projection)
            lastCaptureReceipt = receipt; onCaptureCompletion?(receipt)
        }
    }

    private func reset() {
        invalidate(); driver.cancelRecognition(); driver.stopPlayback()
        phase = .idle; issue = nil; partialText = ""; contextID = nil; activeVoiceName = nil
        activeCaptureMode = nil; selectedWakePhrase = nil; wakePhraseDetected = false; wakeProjection = nil
        captureIdentifier = nil; playbackIdentifier = nil
        endpointAfterSilence = nil
    }

    private func endPlayback(event: LocalPlaybackEvent, issue: LocalVoiceIssue?) {
        let context = contextID
        let identity = playbackIdentifier
        reset(); self.issue = issue
        if let contextID = context { onPlaybackCompletion?(.init(contextID: contextID, playbackID: identity, event: event, issue: issue)) }
    }

    private func validContext(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        && value.utf8.count <= 512 && !value.contains("\0")
    }

    private func invalidate() {
        generation &+= 1
        permission?.cancel(); permission = nil
        preparation?.cancel(); preparation = nil
        deadline?.cancel(); deadline = nil
        silenceDeadline?.cancel(); silenceDeadline = nil
        silenceGeneration &+= 1
    }

    private func recognition(_ event: LocalRecognitionEvent, generation token: UInt64) {
        guard token == generation, phase == .listening || phase == .finishing else { return }
        switch event {
        case let .partial(text), let .final(text):
            guard text.utf8.count <= maxTextBytes else { fail(.textLimit); return }
            let previous = partialText
            if let phrase = selectedWakePhrase {
                if let projection = LocalWakePhrasePolicy.reviewedProjection(in: text, phrase: phrase) {
                    wakePhraseDetected = true; partialText = projection.text; wakeProjection = projection.metadata
                } else {
                    guard !wakePhraseDetected else { fail(.recognitionFailed); return }
                    // Pre-trigger hypotheses remain only in the local SDK. They
                    // never enter a draft, log, model request or visible preview.
                    partialText = ""
                }
            } else { partialText = text }
            if phase == .listening, let silence = endpointAfterSilence,
               partialText != previous, !partialText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                silenceDeadline?.cancel()
                silenceGeneration &+= 1; let silenceTicket = silenceGeneration
                silenceDeadline = clock.schedule(after: silence) { [weak self] in
                    guard let self, self.generation == token, self.silenceGeneration == silenceTicket, self.phase == .listening else { return }
                    self.stopDictation()
                }
            }
            if case .final = event { publish(completion: .recognizedFinal) }
        case .failed: fail(.recognitionFailed)
        case let .issue(issue): fail(issue)
        }
    }

    private func publish(completion: LocalVoiceTranscript.Completion) {
        guard let contextID else { cancel(); return }
        guard selectedWakePhrase == nil || wakePhraseDetected else { fail(.wakePhraseNotHeard); return }
        let text = partialText.trimmingCharacters(in: .whitespacesAndNewlines)
        let identity = captureIdentifier
        let mode = activeCaptureMode ?? .dictation, matched = wakePhraseDetected, projection = wakeProjection
        let result = LocalVoiceTranscript(contextID: contextID, text: text, completion: completion, captureID: identity,
            captureMode: mode, wakeMatched: matched)
        let receipt = LocalVoiceCaptureReceipt(contextID: contextID, captureID: identity,
            outcome: text.isEmpty ? .empty : .transcript, captureMode: mode, wakeMatched: matched, wakeProjection: projection)
        reset()
        lastCaptureReceipt = receipt
        if !text.isEmpty { onTranscript?(result) }
        onCaptureCompletion?(receipt)
    }

    private func fail(_ issue: LocalVoiceIssue) {
        let captureContext = canStopDictation ? contextID : nil
        let identity = captureIdentifier
        let mode = activeCaptureMode ?? .dictation, matched = wakePhraseDetected, projection = wakeProjection
        reset(); self.issue = issue
        if let contextID = captureContext {
            let receipt = LocalVoiceCaptureReceipt(contextID: contextID, captureID: identity, outcome: .failed,
                issue: issue, captureMode: mode, wakeMatched: matched, wakeProjection: projection)
            lastCaptureReceipt = receipt; onCaptureCompletion?(receipt)
        }
    }
}
