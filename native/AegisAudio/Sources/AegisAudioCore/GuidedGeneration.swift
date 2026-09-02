import Foundation
import OSLog
#if canImport(FoundationModels)
import FoundationModels
#endif

public enum GuidedGenerationError: Error, Equatable, Sendable {
    case modelUnavailable
    case invalidInput
    case thermalUnavailable
    case concurrentRequest
    case dispatchRejected
}

#if canImport(FoundationModels) && AEGIS_FOUNDATION_MODELS_MACROS
@available(macOS 26.0, *)
@Generable(description: "A bounded Apple Mail action that requires broker authorization.")
public struct MailAction: Sendable {
    @Guide(description: "Either draft or send.", .anyOf(["draft", "send"]))
    public var action: String

    @Guide(description: "Primary recipient email addresses.", .count(1 ... 10))
    public var recipients: [String]

    @Guide(description: "Optional carbon-copy recipient email addresses.", .maximumCount(10))
    public var cc: [String]

    @Guide(description: "Message subject, at most 200 visible characters.")
    public var subject: String

    @Guide(description: "Message body, at most 8000 visible characters.")
    public var body: String
}

@available(macOS 26.0, *)
@Generable(description: "A bounded Calendar event proposal that requires broker authorization.")
public struct CalendarEvent: Sendable {
    @Guide(description: "Event title, at most 200 visible characters.")
    public var title: String

    @Guide(description: "ISO-8601 start date with timezone.")
    public var startAt: String

    @Guide(description: "ISO-8601 end date with timezone, later than startAt.")
    public var endAt: String

    @Guide(description: "Optional location, at most 300 visible characters.")
    public var location: String

    @Guide(description: "Optional notes, at most 2000 visible characters.")
    public var notes: String
}

@available(macOS 26.0, *)
@Generable(description: "A narrow application action; arbitrary scripts are forbidden.")
public struct ApplicationAction: Sendable {
    @Guide(description: "A reverse-DNS macOS bundle identifier.")
    public var bundleIdentifier: String

    @Guide(description: "Allowed application operation.", .anyOf(["open", "activate"]))
    public var operation: String
}

@available(macOS 26.0, *)
@Generable(description: "The typed outcome of local guided planning.")
public struct GuidedToolPlan: Sendable {
    @Guide(description: "Concise Spanish response for the owner.")
    public var response: String

    @Guide(
        description: "Tool selected by the local model.",
        .anyOf(["none", "mail", "calendar", "application"])
    )
    public var selectedTool: String

    @Guide(description: "True when the secure broker must request owner confirmation.")
    public var requiresConfirmation: Bool
}

@available(macOS 26.0, *)
public enum GuidedToolProposal: Sendable {
    case mail(MailAction)
    case calendar(CalendarEvent)
    case application(ApplicationAction)

    fileprivate var boundedRequest: String? {
        switch self {
        case let .mail(action):
            guard
                ["draft", "send"].contains(action.action),
                (1 ... 10).contains(action.recipients.count),
                action.cc.count <= 10,
                action.recipients.allSatisfy(Self.validEmail),
                action.cc.allSatisfy(Self.validEmail),
                Self.valid(action.subject, maximumCharacters: 200),
                Self.valid(action.body, maximumCharacters: 8_000)
            else { return nil }
            return "\(action.action == "send" ? "Envía" : "Prepara") un correo para "
                + action.recipients.joined(separator: ", ")
                + (action.cc.isEmpty ? "" : ", con copia a " + action.cc.joined(separator: ", "))
                + ", asunto: \(action.subject), contenido: \(action.body)"
        case let .calendar(event):
            guard
                Self.valid(event.title, maximumCharacters: 200),
                Self.valid(event.location, maximumCharacters: 300, permitsEmpty: true),
                Self.valid(event.notes, maximumCharacters: 2_000, permitsEmpty: true),
                Self.iso8601(event.startAt) != nil,
                let start = Self.iso8601(event.startAt),
                let end = Self.iso8601(event.endAt),
                end > start
            else { return nil }
            return "Crea un evento de calendario titulado \(event.title), desde "
                + "\(event.startAt) hasta \(event.endAt), lugar: \(event.location), "
                + "notas: \(event.notes)"
        case let .application(action):
            guard
                ["open", "activate"].contains(action.operation),
                action.bundleIdentifier.range(
                    of: #"^[A-Za-z0-9][A-Za-z0-9.-]{2,199}$"#,
                    options: .regularExpression
                ) != nil
            else { return nil }
            return "\(action.operation == "open" ? "Abre" : "Activa") la aplicación "
                + "con bundle identifier \(action.bundleIdentifier)"
        }
    }

    private static func valid(
        _ value: String,
        maximumCharacters: Int,
        permitsEmpty: Bool = false
    ) -> Bool {
        (permitsEmpty || !value.isEmpty)
            && value.unicodeScalars.count <= maximumCharacters
            && !value.unicodeScalars.contains(where: { $0.value == 0 })
    }

    private static func validEmail(_ value: String) -> Bool {
        value.range(
            of: #"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}$"#,
            options: [.regularExpression, .caseInsensitive]
        ) != nil
    }

    private static func iso8601(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}

@available(macOS 26.0, *)
public protocol GuidedToolDispatching: Sendable {
    func dispatch(_ proposal: GuidedToolProposal) async throws -> UUID
}

@available(macOS 26.0, *)
public struct SignedIPCGuidedToolDispatcher: GuidedToolDispatching, Sendable {
    private let secret: Data

    public init(secret: Data) throws {
        guard secret.count == 32 else { throw LocalIPCError.invalidCredential }
        self.secret = secret
    }

    public func dispatch(_ proposal: GuidedToolProposal) async throws -> UUID {
        guard let text = proposal.boundedRequest, text.utf8.count <= 24_576 else {
            throw GuidedGenerationError.invalidInput
        }
        let secret = self.secret
        return try await Task.detached(priority: .userInitiated) {
            let response = try LocalIPCClient(secret: secret).call(
                method: "swarm.submit",
                payload: ["text": text, "modalities": ["text"]]
            )
            guard let submission = VoiceSubmissionEvent(response: response) else {
                throw GuidedGenerationError.dispatchRejected
            }
            return submission.jobID
        }.value
    }
}

@available(macOS 26.0, *)
public struct MailActionTool: Tool, Sendable {
    public let name = "prepare_mail"
    public let description = "Prepare or send Apple Mail content through the secure Jarvis broker."
    private let dispatcher: any GuidedToolDispatching

    public init(dispatcher: any GuidedToolDispatching) {
        self.dispatcher = dispatcher
    }

    public func call(arguments: MailAction) async throws -> String {
        let jobID = try await dispatcher.dispatch(.mail(arguments))
        return "Secure broker accepted mail job \(jobID.uuidString.lowercased())."
    }
}

@available(macOS 26.0, *)
public struct CalendarEventTool: Tool, Sendable {
    public let name = "create_calendar_event"
    public let description = "Create a Calendar event only through the secure confirmation broker."
    private let dispatcher: any GuidedToolDispatching

    public init(dispatcher: any GuidedToolDispatching) {
        self.dispatcher = dispatcher
    }

    public func call(arguments: CalendarEvent) async throws -> String {
        let jobID = try await dispatcher.dispatch(.calendar(arguments))
        return "Secure broker accepted calendar job \(jobID.uuidString.lowercased())."
    }
}

@available(macOS 26.0, *)
public struct ApplicationActionTool: Tool, Sendable {
    public let name = "silent_executor"
    public let description = (
        "Open or activate an authorized macOS application through the silent executor; "
            + "arbitrary scripts and destructive operations are forbidden."
    )
    private let dispatcher: any GuidedToolDispatching

    public init(dispatcher: any GuidedToolDispatching) {
        self.dispatcher = dispatcher
    }

    public func call(arguments: ApplicationAction) async throws -> String {
        let jobID = try await dispatcher.dispatch(.application(arguments))
        return "Secure broker accepted application job \(jobID.uuidString.lowercased())."
    }
}

@available(macOS 26.0, *)
public actor GuidedGenerationCoordinator {
    private let logger = Logger(
        subsystem: "ai.aegis.audio-core",
        category: "GuidedGeneration"
    )
    private let dispatcher: any GuidedToolDispatching
    private var session: LanguageModelSession?

    public init(dispatcher: any GuidedToolDispatching) {
        self.dispatcher = dispatcher
    }

    public func prewarm() throws {
        try requireSafeThermalState()
        try modelSession().prewarm()
    }

    public func respond(
        to prompt: String,
        maximumResponseTokens: Int = 512
    ) async throws -> GuidedToolPlan {
        guard
            !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            prompt.utf8.count <= 24_576,
            (64 ... 2_048).contains(maximumResponseTokens)
        else { throw GuidedGenerationError.invalidInput }
        try requireSafeThermalState()
        let activeSession = try modelSession()
        guard !activeSession.isResponding else {
            throw GuidedGenerationError.concurrentRequest
        }
        let response = try await activeSession.respond(
            to: prompt,
            generating: GuidedToolPlan.self,
            includeSchemaInPrompt: true,
            options: GenerationOptions(maximumResponseTokens: maximumResponseTokens)
        )
        logger.info("guided_generation_completed typed=true")
        return response.content
    }

    public func resetSession() {
        session = nil
    }

    private func modelSession() throws -> LanguageModelSession {
        guard SystemLanguageModel.default.isAvailable else {
            throw GuidedGenerationError.modelUnavailable
        }
        if let session { return session }
        let tools: [any Tool] = [
            MailActionTool(dispatcher: dispatcher),
            CalendarEventTool(dispatcher: dispatcher),
            ApplicationActionTool(dispatcher: dispatcher),
        ]
        let created = LanguageModelSession(
            model: .default,
            tools: tools,
            instructions: (
                "You are Jarvis. Prefer concise Spanish. Use tools only when the owner asks "
                    + "for an action. Never claim an action succeeded until its secure broker "
                    + "job was accepted. Mail sending and Calendar creation always require "
                    + "confirmation."
            )
        )
        session = created
        return created
    }

    private func requireSafeThermalState() throws {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal, .fair:
            return
        case .serious, .critical:
            resetSession()
            throw GuidedGenerationError.thermalUnavailable
        @unknown default:
            resetSession()
            throw GuidedGenerationError.thermalUnavailable
        }
    }
}
#endif
