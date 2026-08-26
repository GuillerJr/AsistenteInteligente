public enum LocalVoiceCapabilitiesCommand: Equatable, Sendable {
    case describeCapabilities

    private static let commands: Set<String> = [
        "que puedes hacer",
        "que sabes hacer",
        "como puedes ayudarme",
        "what can you do",
        "how can you help me",
    ]

    public static func parse(_ transcript: String) -> Self? {
        commands.contains(LocalVoiceCommandText.normalize(transcript))
            ? .describeCapabilities
            : nil
    }

    public static func spokenResponse(visualControlReady: Bool) -> String {
        if visualControlReady {
            return "Puedo conversar contigo, recordar tus preferencias y consultar este Mac, tu correo, calendario e Internet. Con tu aprobación, también puedo abrir aplicaciones, ejecutar atajos y controlar visualmente una app."
        }
        return "Puedo conversar contigo, recordar tus preferencias y consultar este Mac, tu correo, calendario e Internet. Con tu aprobación, también puedo abrir aplicaciones y ejecutar atajos. El control visual estará disponible cuando actives Pantalla y Control."
    }
}
