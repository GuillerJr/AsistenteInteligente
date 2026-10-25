import Foundation
import Observation

public enum LocalVoicePhase: String, Equatable, Sendable {
    case idle, authorizing, listening, finishing, speaking
}

public enum LocalVoiceIssue: Error, Equatable, Sendable {
    case microphoneDenied, speechDenied, onDeviceUnavailable, captureFailed
    case recognitionFailed, noLocalVoice, playbackFailed, invalidContext, textLimit
    case authorizationTimeout, playbackTimeout, busy, configuration

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
        case .authorizationTimeout: "Se agotó la espera de permisos. Puedes iniciar el dictado de nuevo."
        case .playbackTimeout: "Se detuvo la lectura al alcanzar su límite de tiempo."
        case .busy: "Termina el dictado actual antes de iniciar otra operación de voz."
        case .configuration: "El paquete de Jarvis no incluye la configuración de permisos de voz. Revisa su instalación."
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
    case partial(String), final(String), failed
}

public enum LocalPlaybackEvent: Equatable, Sendable {
    case finished, cancelled, failed
}

public struct LocalVoiceTranscript: Equatable, Sendable {
    public enum Completion: Equatable, Sendable { case recognizedFinal, partialAtStop }
    public let contextID: String
    public let text: String
    public let completion: Completion
    public let source = "on_device_speech"
    public let requiresReview = true
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
    func supportsOnDeviceRecognition(localeIdentifier: String) -> Bool
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
    @ObservationIgnored public var onTranscript: (@MainActor (LocalVoiceTranscript) -> Void)?

    @ObservationIgnored private let driver: any LocalVoiceDriver
    @ObservationIgnored private let clock: any LocalVoiceClock
    @ObservationIgnored private let captureSeconds: TimeInterval
    @ObservationIgnored private let playbackSeconds: TimeInterval
    @ObservationIgnored private let maxTextBytes: Int
    @ObservationIgnored private var generation: UInt64 = 0
    @ObservationIgnored private var permission: (any LocalVoiceCancellation)?
    @ObservationIgnored private var deadline: (any LocalVoiceCancellation)?

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
        let driver = driver, permission = permission, deadline = deadline
        Task { @MainActor in
            permission?.cancel(); deadline?.cancel()
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
        case .authorizing: return "Esperando tu decisión de permisos…"
        case .listening: return "Dictando en este Mac · Máximo 30 segundos"
        case .finishing: return "Finalizando el dictado local…"
        case .speaking: return "Leyendo con una voz local…"
        }
    }

    public func startDictation(contextID: String, localeIdentifier: String = "es-ES") {
        guard canStartDictation else { issue = .busy; return }
        guard validContext(contextID) else { issue = .invalidContext; return }
        invalidate()
        driver.stopPlayback()
        issue = nil; partialText = ""; activeVoiceName = nil
        self.contextID = contextID; phase = .authorizing
        let token = generation
        deadline = clock.schedule(after: 30) { [weak self] in
            guard let self, self.generation == token, self.phase == .authorizing else { return }
            self.fail(.authorizationTimeout)
        }
        let request = driver.requestPermissions { [weak self] permissions in
            guard let self, self.generation == token, self.phase == .authorizing else { return }
            self.permission?.cancel(); self.permission = nil
            self.deadline?.cancel(); self.deadline = nil
            guard !permissions.configurationMissing else { self.fail(.configuration); return }
            guard permissions.speech == .authorized else { self.fail(.speechDenied); return }
            guard permissions.microphone == .authorized else { self.fail(.microphoneDenied); return }
            guard self.driver.supportsOnDeviceRecognition(localeIdentifier: localeIdentifier) else {
                self.fail(.onDeviceUnavailable); return
            }
            self.phase = .listening
            do {
                try self.driver.beginRecognition(localeIdentifier: localeIdentifier) { [weak self] event in
                    self?.recognition(event, generation: token)
                }
                // A fake or synchronous backend can finish during beginRecognition.
                guard self.generation == token, self.phase == .listening else { return }
                self.deadline = self.clock.schedule(after: self.captureSeconds) { [weak self] in
                    guard let self, self.generation == token, self.phase == .listening else { return }
                    self.stopDictation()
                }
            } catch { self.fail((error as? LocalVoiceIssue) ?? .captureFailed) }
        }
        if phase == .authorizing, generation == token { permission = request }
        else { request.cancel() }
    }

    public func stopDictation() {
        if phase == .authorizing || phase == .finishing { cancel(); return }
        guard phase == .listening else { return }
        phase = .finishing
        deadline?.cancel(); deadline = nil
        let token = generation
        driver.finishRecognition()
        guard generation == token, phase == .finishing else { return }
        deadline = clock.schedule(after: 3) { [weak self] in
            guard let self, self.generation == token, self.phase == .finishing else { return }
            self.publish(completion: .partialAtStop)
        }
    }

    public func play(text: String, contextID: String, localeIdentifier: String = "es-ES", voiceID: String? = nil) {
        guard phase == .idle || phase == .speaking else { issue = .busy; return }
        guard validContext(contextID) else { issue = .invalidContext; return }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.utf8.count <= maxTextBytes else { issue = .textLimit; return }
        let voices = driver.localVoices(localeIdentifier: localeIdentifier)
        let voice = voiceID.flatMap { id in voices.first { $0.id == id } } ?? (voiceID == nil ? voices.first : nil)
        guard let voice else { issue = .noLocalVoice; return }
        invalidate(); driver.stopPlayback()
        phase = .speaking; issue = nil; self.contextID = contextID; activeVoiceName = voice.name
        let token = generation
        do {
            try driver.beginPlayback(text: text, voice: voice) { [weak self] event in
                guard let self, self.generation == token, self.phase == .speaking else { return }
                if event == .failed { self.fail(.playbackFailed) }
                else { self.cancel() }
            }
            guard generation == token, phase == .speaking else { return }
            deadline = clock.schedule(after: playbackSeconds) { [weak self] in
                guard let self, self.generation == token, self.phase == .speaking else { return }
                self.fail(.playbackTimeout)
            }
        } catch { fail((error as? LocalVoiceIssue) ?? .playbackFailed) }
    }

    /// The view calls this on conversation change, window close, logout and shutdown.
    public func cancel() {
        invalidate(); driver.cancelRecognition(); driver.stopPlayback()
        phase = .idle; issue = nil; partialText = ""; contextID = nil; activeVoiceName = nil
    }

    private func validContext(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        && value.utf8.count <= 512 && !value.contains("\0")
    }

    private func invalidate() {
        generation &+= 1
        permission?.cancel(); permission = nil
        deadline?.cancel(); deadline = nil
    }

    private func recognition(_ event: LocalRecognitionEvent, generation token: UInt64) {
        guard token == generation, phase == .listening || phase == .finishing else { return }
        switch event {
        case let .partial(text), let .final(text):
            guard text.utf8.count <= maxTextBytes else { fail(.textLimit); return }
            partialText = text
            if case .final = event { publish(completion: .recognizedFinal) }
        case .failed: fail(.recognitionFailed)
        }
    }

    private func publish(completion: LocalVoiceTranscript.Completion) {
        guard let contextID else { cancel(); return }
        let text = partialText.trimmingCharacters(in: .whitespacesAndNewlines)
        let result = LocalVoiceTranscript(contextID: contextID, text: text, completion: completion)
        cancel()
        if !text.isEmpty { onTranscript?(result) }
    }

    private func fail(_ issue: LocalVoiceIssue) {
        cancel(); self.issue = issue
    }
}
