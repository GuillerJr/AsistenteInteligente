import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

public enum LocalInferenceHelperError: Error, Equatable, Sendable {
    case modelUnavailable
    case invalidInput
    case thermalUnavailable
    case concurrentRequest
    case invalidModelOutput
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
public actor LocalInferenceHelper {
    public static let maximumInputBytes = 24_576
    public static let maximumOutputBytes = 24_576
    public static let maximumResponseTokens = 4_096

    private var session: LanguageModelSession?
    private var activeInstructions: String?

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
