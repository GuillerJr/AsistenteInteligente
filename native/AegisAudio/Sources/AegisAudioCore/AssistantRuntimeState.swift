public enum DaemonConnectionState: Equatable, Sendable {
    case unknown
    case checking
    case online
    case offline
    case securityFailure

    public var title: String {
        switch self {
        case .unknown:
            "sin comprobar"
        case .checking:
            "comprobando"
        case .online:
            "conectado"
        case .offline:
            "desconectado"
        case .securityFailure:
            "bloqueado"
        }
    }

    public var symbol: String {
        switch self {
        case .unknown, .checking:
            "circle.dotted"
        case .online:
            "checkmark.circle.fill"
        case .offline:
            "circle"
        case .securityFailure:
            "exclamationmark.shield.fill"
        }
    }
}

public enum SecurityMonitorState: String, Sendable {
    case unknown
    case checking
    case intact
    case compromised
    case unavailable

    public var title: String {
        switch self {
        case .unknown:
            "sin comprobar"
        case .checking:
            "comprobando"
        case .intact:
            "íntegra"
        case .compromised:
            "comprometida"
        case .unavailable:
            "no disponible"
        }
    }

    public var symbol: String {
        switch self {
        case .unknown, .checking:
            "circle.dotted"
        case .intact:
            "checkmark.shield.fill"
        case .compromised:
            "exclamationmark.shield.fill"
        case .unavailable:
            "shield.slash"
        }
    }
}

public enum ProviderReadinessState: String, Sendable {
    case unknown
    case checking
    case configured
    case missing
    case unavailable

    public var title: String {
        switch self {
        case .unknown: "sin comprobar"
        case .checking: "comprobando"
        case .configured: "configurado"
        case .missing: "sin credencial"
        case .unavailable: "no verificable"
        }
    }
}

public enum VoiceTurnState: Equatable, Sendable {
    case idle
    case listening
    case followingUp
    case submitting
    case processing
    case awaitingAuthorization
    case speaking
    case completed
    case failed

    public var title: String {
        switch self {
        case .idle:
            "listo"
        case .listening:
            "escuchando"
        case .followingUp:
            "esperando respuesta"
        case .submitting:
            "enviando"
        case .processing:
            "procesando"
        case .awaitingAuthorization:
            "esperando autorización"
        case .speaking:
            "respondiendo"
        case .completed:
            "completado"
        case .failed:
            "falló"
        }
    }

    public var symbol: String {
        switch self {
        case .idle:
            "waveform"
        case .listening:
            "waveform.circle.fill"
        case .followingUp:
            "ear.badge.waveform"
        case .submitting:
            "arrow.up.circle.fill"
        case .processing:
            "brain.head.profile.fill"
        case .awaitingAuthorization:
            "touchid"
        case .speaking:
            "speaker.wave.2.circle.fill"
        case .completed:
            "checkmark.circle.fill"
        case .failed:
            "exclamationmark.circle.fill"
        }
    }

    public var isBusy: Bool {
        switch self {
        case .listening, .followingUp, .submitting, .processing:
            true
        case .idle, .awaitingAuthorization, .speaking, .completed, .failed:
            false
        }
    }
}
