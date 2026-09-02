import Foundation
import CoreVideo
import Vision
#if canImport(FoundationModels)
import FoundationModels
#endif

public enum LocalInferenceHelperError: Error, Equatable, Sendable {
    case modelUnavailable
    case invalidInput
    case thermalUnavailable
    case concurrentRequest
    case invalidModelOutput
    case perceptionFailed
}

public enum LocalOCRPerception {
    public static let maximumContextBytes = 8_192

    public static func context(from pixelBuffer: CVPixelBuffer) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .fast
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["es-ES", "en-US"]
        request.minimumTextHeight = 0.012
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw LocalInferenceHelperError.perceptionFailed
        }
        let lines = (request.results ?? []).prefix(96).compactMap { observation -> String? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            let label = candidate.string.split(whereSeparator: { $0.isWhitespace })
                .joined(separator: " ")
                .replacingOccurrences(of: "\n", with: " ")
            guard !label.isEmpty else { return nil }
            return String(
                format: "- %@ @ x=%.4f y=%.4f w=%.4f h=%.4f confidence=%.3f",
                String(label.prefix(512)),
                box.minX,
                1.0 - box.maxY,
                box.width,
                box.height,
                candidate.confidence
            )
        }
        let context = (["# OCR local verificado"] + lines).joined(separator: "\n")
        let encoded = Data(context.utf8)
        guard encoded.count > 0 else { throw LocalInferenceHelperError.perceptionFailed }
        if encoded.count <= maximumContextBytes { return context }
        return String(
            decoding: encoded.prefix(maximumContextBytes),
            as: UTF8.self
        ).split(separator: "\n", omittingEmptySubsequences: false).dropLast()
            .joined(separator: "\n")
    }
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
private struct LocalFoundationToolDispatcher: Sendable {
    let secret: Data

    init(secret: Data) throws {
        guard secret.count == 32 else { throw LocalIPCError.invalidCredential }
        self.secret = secret
    }

    func submit(_ text: String) async throws -> String {
        guard
            !text.isEmpty,
            text.utf8.count <= LocalInferenceHelper.maximumInputBytes,
            !text.unicodeScalars.contains(where: { $0.value == 0 })
        else { throw LocalInferenceHelperError.invalidInput }
        let secret = self.secret
        return try await Task.detached(priority: .userInitiated) {
            let response = try LocalIPCClient(secret: secret).call(
                method: "swarm.submit",
                payload: ["text": text, "modalities": ["text"]]
            )
            guard let submission = VoiceSubmissionEvent(response: response) else {
                throw LocalInferenceHelperError.invalidModelOutput
            }
            return "Trabajo local aceptado: \(submission.jobID.uuidString.lowercased())"
        }.value
    }
}

@available(macOS 26.0, *)
private struct LocalMailFoundationTool: Tool, Sendable {
    let name = "mail"
    let description = "Prepara o envía correo mediante el broker local seguro de Jarvis."
    let parameters = GenerationSchema(
        type: GeneratedContent.self,
        description: "Acción acotada de Apple Mail.",
        properties: [
            .init(
                name: "action",
                description: "Operación solicitada.",
                type: String.self,
                guides: [.anyOf(["draft", "send"])]
            ),
            .init(
                name: "recipients",
                description: "Destinatarios principales.",
                type: [String].self,
                guides: [.count(1 ... 10)]
            ),
            .init(
                name: "cc",
                description: "Destinatarios en copia, lista vacía si no aplica.",
                type: [String].self,
                guides: [.maximumCount(10)]
            ),
            .init(name: "subject", description: "Asunto del correo.", type: String.self),
            .init(name: "body", description: "Cuerpo del correo.", type: String.self),
        ]
    )
    private let dispatcher: LocalFoundationToolDispatcher

    init(dispatcher: LocalFoundationToolDispatcher) {
        self.dispatcher = dispatcher
    }

    func call(arguments: GeneratedContent) async throws -> String {
        let action = try arguments.value(String.self, forProperty: "action")
        let recipients = try arguments.value([String].self, forProperty: "recipients")
        let cc = try arguments.value([String].self, forProperty: "cc")
        let subject = try arguments.value(String.self, forProperty: "subject")
        let body = try arguments.value(String.self, forProperty: "body")
        guard
            ["draft", "send"].contains(action),
            (1 ... 10).contains(recipients.count),
            cc.count <= 10,
            (recipients + cc).allSatisfy(Self.validEmail),
            Self.valid(subject, maximumCharacters: 200),
            Self.valid(body, maximumCharacters: 8_000)
        else { throw LocalInferenceHelperError.invalidInput }
        let request = "\(action == "send" ? "Envía" : "Prepara") un correo para "
            + recipients.joined(separator: ", ")
            + (cc.isEmpty ? "" : ", con copia a " + cc.joined(separator: ", "))
            + ", asunto: \(subject), contenido: \(body)"
        return try await dispatcher.submit(request)
    }

    private static func valid(_ value: String, maximumCharacters: Int) -> Bool {
        !value.isEmpty && value.count <= maximumCharacters
            && !value.unicodeScalars.contains(where: { $0.value == 0 })
    }

    private static func validEmail(_ value: String) -> Bool {
        value.range(
            of: #"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}$"#,
            options: [.regularExpression, .caseInsensitive]
        ) != nil
    }
}

@available(macOS 26.0, *)
private struct LocalCalendarFoundationTool: Tool, Sendable {
    let name = "calendar"
    let description = "Propone un evento de Calendar mediante el broker local seguro."
    let parameters = GenerationSchema(
        type: GeneratedContent.self,
        description: "Evento acotado de Apple Calendar.",
        properties: [
            .init(name: "title", description: "Título del evento.", type: String.self),
            .init(name: "start_at", description: "Inicio ISO-8601.", type: String.self),
            .init(name: "end_at", description: "Fin ISO-8601.", type: String.self),
            .init(name: "location", description: "Lugar o cadena vacía.", type: String.self),
            .init(name: "notes", description: "Notas o cadena vacía.", type: String.self),
        ]
    )
    private let dispatcher: LocalFoundationToolDispatcher

    init(dispatcher: LocalFoundationToolDispatcher) {
        self.dispatcher = dispatcher
    }

    func call(arguments: GeneratedContent) async throws -> String {
        let title = try arguments.value(String.self, forProperty: "title")
        let startAt = try arguments.value(String.self, forProperty: "start_at")
        let endAt = try arguments.value(String.self, forProperty: "end_at")
        let location = try arguments.value(String.self, forProperty: "location")
        let notes = try arguments.value(String.self, forProperty: "notes")
        let formatter = ISO8601DateFormatter()
        guard
            !title.isEmpty,
            title.count <= 200,
            location.count <= 300,
            notes.count <= 2_000,
            let start = formatter.date(from: startAt),
            let end = formatter.date(from: endAt),
            end > start
        else { throw LocalInferenceHelperError.invalidInput }
        return try await dispatcher.submit(
            "Crea un evento titulado \(title), desde \(startAt) hasta \(endAt), "
                + "lugar: \(location), notas: \(notes)"
        )
    }
}

@available(macOS 26.0, *)
private struct LocalSilentExecutorFoundationTool: Tool, Sendable {
    let name = "silent_executor"
    let description = "Abre o activa una aplicación autorizada sin scripts arbitrarios."
    let parameters = GenerationSchema(
        type: GeneratedContent.self,
        description: "Acción silenciosa y acotada sobre una aplicación.",
        properties: [
            .init(
                name: "operation",
                description: "Operación permitida.",
                type: String.self,
                guides: [.anyOf(["open", "activate"])]
            ),
            .init(
                name: "bundle_identifier",
                description: "Bundle identifier reverse-DNS.",
                type: String.self
            ),
        ]
    )
    private let dispatcher: LocalFoundationToolDispatcher

    init(dispatcher: LocalFoundationToolDispatcher) {
        self.dispatcher = dispatcher
    }

    func call(arguments: GeneratedContent) async throws -> String {
        let operation = try arguments.value(String.self, forProperty: "operation")
        let bundleIdentifier = try arguments.value(
            String.self,
            forProperty: "bundle_identifier"
        )
        guard
            ["open", "activate"].contains(operation),
            bundleIdentifier.range(
                of: #"^[A-Za-z0-9][A-Za-z0-9.-]{2,199}$"#,
                options: .regularExpression
            ) != nil
        else { throw LocalInferenceHelperError.invalidInput }
        return try await dispatcher.submit(
            "\(operation == "open" ? "Abre" : "Activa") la aplicación con bundle "
                + "identifier \(bundleIdentifier)"
        )
    }
}

@available(macOS 26.0, *)
public actor LocalInferenceHelper {
    public static let maximumInputBytes = 24_576
    public static let maximumOutputBytes = 24_576
    public static let maximumResponseTokens = 4_096

    private var session: LanguageModelSession?
    private var activeInstructions: String?
    private var toolSession: LanguageModelSession?
    private var activeToolInstructions: String?

    public init() {}

    public static var isAvailable: Bool {
        SystemLanguageModel.default.isAvailable
    }

    public func prewarm(instructions: String, promptPrefix: String? = nil) throws {
        try requireSafeThermalState()
        let modelSession = try sessionFor(instructions: instructions)
        if let promptPrefix {
            guard Self.valid(promptPrefix) else {
                throw LocalInferenceHelperError.invalidInput
            }
            modelSession.prewarm(promptPrefix: Prompt(promptPrefix))
        } else {
            modelSession.prewarm()
        }
    }

    @discardableResult
    public func generateDraft(
        instructions: String,
        prompt: String,
        maximumResponseTokens: Int? = nil,
        temperature: Double? = nil,
        onSnapshot: @Sendable (String) async throws -> Void
    ) async throws -> String {
        guard
            Self.valid(instructions),
            Self.valid(prompt),
            maximumResponseTokens.map({ (1 ... Self.maximumResponseTokens).contains($0) })
                ?? true,
            temperature.map({ $0.isFinite && (0 ... 2).contains($0) }) ?? true
        else {
            throw LocalInferenceHelperError.invalidInput
        }
        try requireSafeThermalState()
        let modelSession = try sessionFor(instructions: instructions)
        guard !modelSession.isResponding else {
            throw LocalInferenceHelperError.concurrentRequest
        }
        return try await stream(
            session: modelSession,
            prompt: prompt,
            maximumResponseTokens: maximumResponseTokens,
            temperature: temperature,
            onSnapshot: onSnapshot
        )
    }

    @discardableResult
    public func generateToolAugmented(
        instructions: String,
        prompt: String,
        ipcSecret: Data,
        maximumResponseTokens: Int? = nil,
        temperature: Double? = nil,
        onSnapshot: @Sendable (String) async throws -> Void
    ) async throws -> String {
        guard
            Self.valid(instructions),
            Self.valid(prompt),
            maximumResponseTokens.map({ (1 ... Self.maximumResponseTokens).contains($0) })
                ?? true,
            temperature.map({ $0.isFinite && (0 ... 2).contains($0) }) ?? true
        else {
            throw LocalInferenceHelperError.invalidInput
        }
        try requireSafeThermalState()
        let modelSession = try toolSessionFor(
            instructions: instructions,
            ipcSecret: ipcSecret
        )
        return try await stream(
            session: modelSession,
            prompt: prompt,
            maximumResponseTokens: maximumResponseTokens,
            temperature: temperature,
            onSnapshot: onSnapshot
        )
    }

    private func stream(
        session modelSession: LanguageModelSession,
        prompt: String,
        maximumResponseTokens: Int?,
        temperature: Double?,
        onSnapshot: @Sendable (String) async throws -> Void
    ) async throws -> String {
        guard !modelSession.isResponding else {
            throw LocalInferenceHelperError.concurrentRequest
        }
        let options = GenerationOptions(
            temperature: temperature,
            maximumResponseTokens: maximumResponseTokens
        )
        var latest = ""
        for try await snapshot in modelSession.streamResponse(to: prompt, options: options) {
            try Task.checkCancellation()
            try requireSafeThermalState()
            let content = snapshot.content
            guard
                content.hasPrefix(latest),
                content.utf8.count <= Self.maximumOutputBytes
            else {
                throw LocalInferenceHelperError.invalidModelOutput
            }
            if content != latest {
                latest = content
                try await onSnapshot(content)
            }
        }
        guard !latest.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw LocalInferenceHelperError.invalidModelOutput
        }
        return latest
    }

    public func resetSession() {
        session = nil
        activeInstructions = nil
        toolSession = nil
        activeToolInstructions = nil
    }

    private func sessionFor(instructions: String) throws -> LanguageModelSession {
        guard Self.isAvailable, Self.valid(instructions) else {
            throw LocalInferenceHelperError.modelUnavailable
        }
        if let session, activeInstructions == instructions {
            return session
        }
        let created = LanguageModelSession(
            model: .default,
            tools: [],
            instructions: instructions
        )
        session = created
        activeInstructions = instructions
        return created
    }

    private func toolSessionFor(
        instructions: String,
        ipcSecret: Data
    ) throws -> LanguageModelSession {
        guard Self.isAvailable, Self.valid(instructions) else {
            throw LocalInferenceHelperError.modelUnavailable
        }
        if let toolSession, activeToolInstructions == instructions {
            return toolSession
        }
        let dispatcher = try LocalFoundationToolDispatcher(secret: ipcSecret)
        let tools: [any Tool] = [
            LocalMailFoundationTool(dispatcher: dispatcher),
            LocalCalendarFoundationTool(dispatcher: dispatcher),
            LocalSilentExecutorFoundationTool(dispatcher: dispatcher),
        ]
        let created = LanguageModelSession(
            model: .default,
            tools: tools,
            instructions: instructions + "\n"
                + "Usa únicamente las herramientas nativas enlazadas. Correo, calendario y "
                + "automatización silenciosa siempre pasan por el broker firmado. Nunca "
                + "declares éxito antes de recibir el identificador del trabajo."
        )
        toolSession = created
        activeToolInstructions = instructions
        return created
    }

    private func requireSafeThermalState() throws {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal, .fair:
            return
        case .serious, .critical:
            resetSession()
            throw LocalInferenceHelperError.thermalUnavailable
        @unknown default:
            resetSession()
            throw LocalInferenceHelperError.thermalUnavailable
        }
    }

    private static func valid(_ value: String) -> Bool {
        !value.isEmpty
            && value.utf8.count <= maximumInputBytes
            && !value.unicodeScalars.contains(where: { $0.value == 0 })
    }
}
#endif
