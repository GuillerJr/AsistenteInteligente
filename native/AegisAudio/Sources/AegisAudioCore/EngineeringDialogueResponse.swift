import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

public enum EngineeringDialogueResponse {
    public static let fallbackQuestion = "¿Puedes concretar qué necesitas y a qué te refieres?"

    /// A clarification is one question, never a guessed project followed by a questionnaire.
    /// This validates presentation, not the semantic accuracy of the model's decision.
    public static func clarification(_ response: String) -> String {
        guard let end = response.firstIndex(of: "?") else { return fallbackQuestion }
        let prefix = response[...end]
        let question = String(prefix[(prefix.lastIndex(of: "¿") ?? prefix.startIndex)...])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, question.count <= 320, !question.contains("\n"),
              !question.contains("`") else { return fallbackQuestion }
        return question
    }
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
extension LocalInferenceHelper {
    private static let engineeringSchema = GenerationSchema(
        type: GeneratedContent.self,
        description: "Respuesta de Jarvis al último turno de una conversación.",
        properties: [
            .init(
                name: "needsRepository",
                description: "True SOLO si la petición pregunta por archivos o implementación "
                    + "del repositorio existente. False para charla, conceptos, ideas, código nuevo "
                    + "y referencias ambiguas sin un objetivo en el historial.",
                type: Bool.self
            ),
            .init(
                name: "needsClarification",
                description: "True solo si falta el objetivo o no se puede resolver una referencia "
                    + "con el historial. No pidas detalles opcionales para empezar a ayudar. "
                    + "Una carpeta vacía NO impide proponer una solución ni explicar conceptos.",
                type: Bool.self
            ),
            .init(
                name: "response",
                description: "Si needsRepository=true: cadena vacía; se consultará evidencia. "
                    + "Si needsClarification=true: UNA pregunta corta sin propuesta. "
                    + "Si false: responde directamente a la petición actual, usando el historial "
                    + "y sus correcciones. No recites el inventario. No inventes hechos ni acciones.",
                type: String.self
            ),
        ]
    )

    func engineeringResponse(
        session: LanguageModelSession,
        prompt: String,
        reference: String?,
        maximumResponseTokens: Int?,
        temperature: Double?,
        onSnapshot: @Sendable (String) async throws -> Void
    ) async throws -> String {
        var latest = ""
        var latestContent: GeneratedContent?
        var reportedProgress = false
        let initialTranscript = session.transcript
        let options = GenerationOptions(
            temperature: temperature, maximumResponseTokens: maximumResponseTokens
        )
        for try await snapshot in session.streamResponse(
            to: prompt, schema: Self.engineeringSchema, options: options
        ) {
            try Task.checkCancellation()
            switch ProcessInfo.processInfo.thermalState {
            case .nominal, .fair: break
            default: throw LocalInferenceHelperError.thermalUnavailable
            }
            if !reportedProgress {
                // An empty snapshot is transport progress, not a first answer/token.
                // Python does not publish an empty delta or count it as first partial.
                try await onSnapshot("")
                reportedProgress = true
            }
            latestContent = snapshot.content
            guard let repository = try? snapshot.content.value(Bool.self, forProperty: "needsRepository"),
                  let clarify = try? snapshot.content.value(Bool.self, forProperty: "needsClarification"),
                  let response = try? snapshot.content.value(String.self, forProperty: "response")
            else { continue } // Partially generated fields are not yet a valid result.
            guard response.utf8.count <= Self.maximumOutputBytes else {
                throw LocalInferenceHelperError.invalidModelOutput
            }
            // Clarifications are validated as a whole before any text reaches the CLI.
            if !repository && !clarify && response != latest {
                guard response.hasPrefix(latest) else {
                    throw LocalInferenceHelperError.invalidModelOutput
                }
                latest = response
                try await onSnapshot(response)
            }
        }
        guard let content = latestContent else { throw LocalInferenceHelperError.invalidModelOutput }
        let repository = try content.value(Bool.self, forProperty: "needsRepository")
        let clarify = try content.value(Bool.self, forProperty: "needsClarification")
        let response = try content.value(String.self, forProperty: "response")
        if repository && !clarify {
            // Retrieve context only after the model identifies an existing-repository task.
            // The first pass cannot be distracted by paths, empty inventories or old RAG hits.
            let groundedPrompt = (reference ?? "No hay evidencia de archivos disponible.")
                + "\n\nPetición actual del usuario:\n" + prompt
            guard groundedPrompt.utf8.count <= Self.maximumInputBytes else {
                throw LocalInferenceHelperError.invalidInput
            }
            return try await stream(
                session: LanguageModelSession(model: .default, tools: [], transcript: initialTranscript),
                prompt: groundedPrompt, maximumResponseTokens: maximumResponseTokens,
                temperature: temperature, onSnapshot: onSnapshot
            )
        }
        let rendered = clarify ? EngineeringDialogueResponse.clarification(response) : response
        guard !rendered.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              rendered.hasPrefix(latest), rendered.utf8.count <= Self.maximumOutputBytes else {
            throw LocalInferenceHelperError.invalidModelOutput
        }
        if rendered != latest { try await onSnapshot(rendered) }
        return rendered
    }
}
#endif
