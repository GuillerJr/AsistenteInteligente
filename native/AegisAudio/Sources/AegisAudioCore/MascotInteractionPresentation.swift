import Foundation

/// Content for the mascot comes from runtime events, never simulated progress.
public struct MascotInteractionPresentation: Equatable, Sendable {
    public let title: String
    public let message: String
    public let requiresReview: Bool

    public static func resolve(
        sessionAvailable: Bool, state: VoiceTurnState,
        reviewMessage: String? = nil, question: String? = nil,
        input: String? = nil, response: String? = nil, error: String? = nil,
        activeRoles: Set<IPCSwarmAgentRole> = [], notice: String? = nil
    ) -> Self {
        guard sessionAvailable else {
            return Self(title: "En pausa", message: "Desbloquea tu Mac para conversar.", requiresReview: false)
        }
        if let reviewMessage {
            return Self(title: "Necesito tu decisión", message: bounded(reviewMessage), requiresReview: true)
        }
        if let question {
            return Self(title: "Una pregunta", message: bounded(question), requiresReview: true)
        }
        if let error, !error.isEmpty {
            return Self(title: "No pude completarlo", message: bounded(error), requiresReview: false)
        }
        switch state {
        case .listening, .followingUp:
            return Self(title: "Te escucho", message: "Habla con normalidad. Mostraré aquí lo que entendí.", requiresReview: false)
        case .submitting:
            return Self(title: "Enviando tu solicitud", message: bounded(input ?? "Preparando la conversación…"), requiresReview: false)
        case .processing, .speaking:
            let title: String
            if state == .speaking { title = "Te respondo" }
            else if activeRoles.contains(.vision) { title = "Analizando la imagen" }
            else if activeRoles.contains(.synthesizer) { title = "Preparando la respuesta" }
            else if activeRoles.contains(.criticalReasoner) { title = "Revisando la solicitud" }
            else if activeRoles.contains(.planner) { title = "Planificando" }
            else { title = "Trabajando en tu solicitud" }
            return Self(title: title, message: bounded(response ?? input ?? "Esperando la respuesta de Jarvis…"), requiresReview: false)
        default:
            if let notice {
                return Self(title: "Un aviso para ti", message: bounded(notice), requiresReview: false)
            }
            return Self(title: response == nil ? "¿En qué te ayudo?" : "Jarvis",
                        message: bounded(response ?? "Escríbeme, arrastra un archivo o haz doble clic en mí para hablar."),
                        requiresReview: false)
        }
    }

    private static func bounded(_ text: String) -> String { String(text.prefix(2_000)) }
}
