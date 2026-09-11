import Foundation

/// One privacy-safe presentation shared by the notch, HUD and menu bar.
/// Derived from runtime evidence; it never grants permission or starts work.
public struct AssistantPresentation: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case securityBlocked, connecting, offline, securityChecking, securityUnavailable
        case brainChecking, brainMissing, brainUnavailable, interrupting, failure
        case approval, browserSelection, listening, followingUp, submitting, processing
        case speaking, completed, working, ambient, idle, cancelled
    }

    public enum Tone: Equatable, Sendable { case neutral, active, warning, failure, success }

    public let kind: Kind
    public let title: String
    public let compactTitle: String
    public let detail: String
    public let symbol: String
    public let tone: Tone

    public var isAnimated: Bool {
        [.connecting, .securityChecking, .brainChecking, .interrupting, .listening,
         .followingUp, .submitting, .processing, .speaking, .working, .ambient].contains(kind)
    }

    public var isProminent: Bool { kind != .idle && kind != .ambient && kind != .completed }

    public var accessibilityDescription: String { "Jarvis. \(title). \(detail)" }

    public static func resolve(
        daemon: DaemonConnectionState,
        security: SecurityMonitorState,
        provider: ProviderReadinessState,
        localBrainAvailable: Bool,
        voice: VoiceTurnState,
        failureCode: String? = nil,
        interrupting: Bool = false,
        approvalPending: Bool = false,
        browserSelectionPending: Bool = false,
        ambientListening: Bool = false,
        activity: [IPCSwarmAgentRole: Int] = [:]
    ) -> Self {
        if security == .compromised || daemon == .securityFailure {
            return .init(kind: .securityBlocked, title: "Ejecución bloqueada", compactTitle: "Bloqueo de seguridad",
                         detail: "La verificación de seguridad falló. Revisa el servicio antes de continuar.",
                         symbol: "exclamationmark.shield.fill", tone: .failure)
        }
        switch daemon {
        case .unknown, .checking:
            return .init(kind: .connecting, title: "Conectando con Jarvis", compactTitle: "Conectando",
                         detail: "Comprobando el servicio local. Aún no está listo para recibir solicitudes.",
                         symbol: "arrow.triangle.2.circlepath", tone: .warning)
        case .offline:
            return .init(kind: .offline, title: "Servicio desconectado", compactTitle: "Sin conexión",
                         detail: "No hay conexión con el servicio local. Actualiza el estado desde el menú de Jarvis.",
                         symbol: "bolt.slash.fill", tone: .warning)
        case .online, .securityFailure: break
        }
        switch security {
        case .unknown, .checking:
            return .init(kind: .securityChecking, title: "Verificando seguridad", compactTitle: "Verificando seguridad",
                         detail: "El servicio está conectado; falta comprobar su integridad.",
                         symbol: "shield.lefthalf.filled", tone: .warning)
        case .unavailable:
            return .init(kind: .securityUnavailable, title: "Seguridad no verificable", compactTitle: "Seguridad sin verificar",
                         detail: "No se pudo verificar la auditoría. Actualiza el estado antes de continuar.",
                         symbol: "shield.slash", tone: .warning)
        case .intact, .compromised: break
        }
        if interrupting {
            return .init(kind: .interrupting, title: "Interrumpiendo el turno", compactTitle: "Interrumpiendo",
                         detail: "Deteniendo la respuesta anterior antes de volver a escuchar.",
                         symbol: "pause.circle.fill", tone: .warning)
        }
        // A spoken error is still an error, not a successful answer.
        if voice == .failed || (voice == .speaking && failureCode != nil) {
            return .init(kind: .failure, title: LocalVoiceTurnFeedback.shortJobFailureTitle(for: failureCode),
                         compactTitle: failureCode == "job_status_unavailable"
                            ? "Resultado sin confirmar" : "Solicitud no completada",
                         detail: failureDetail(for: failureCode),
                         symbol: "exclamationmark.circle.fill", tone: .failure)
        }
        if !localBrainAvailable {
            switch provider {
            case .unknown, .checking:
                return .init(kind: .brainChecking, title: "Verificando inferencia", compactTitle: "Verificando cerebro",
                             detail: "Comprobando la configuración del proveedor. Todavía no hay una ruta confirmada.",
                             symbol: "brain", tone: .warning)
            case .missing:
                return .init(kind: .brainMissing, title: "Inferencia sin configurar", compactTitle: "Configura el cerebro",
                             detail: "Configura NVIDIA o habilita una ruta local compatible en los ajustes de Jarvis.",
                             symbol: "brain", tone: .warning)
            case .unavailable:
                return .init(kind: .brainUnavailable, title: "Inferencia no verificable", compactTitle: "Cerebro no verificable",
                             detail: "No se pudo comprobar la configuración. Revisa el proveedor y el acceso al llavero.",
                             symbol: "brain", tone: .warning)
            case .configured: break
            }
        }
        if approvalPending || voice == .awaitingAuthorization {
            return .init(kind: .approval, title: "Autorización pendiente", compactTitle: "Revisa la aprobación",
                         detail: "Revisa la acción en la ventana de aprobación. Solo continúa si reconoces la solicitud.",
                         symbol: "hand.raised.fill", tone: .warning)
        }
        if browserSelectionPending {
            return .init(kind: .browserSelection, title: "Elige un navegador", compactTitle: "Elige navegador",
                         detail: "Selecciona dónde continuar la solicitud. Aún no se ha abierto la búsqueda.",
                         symbol: "safari", tone: .warning)
        }
        switch voice {
        case .listening:
            return .init(kind: .listening, title: "Te escucho", compactTitle: "Te escucho",
                         detail: "Habla con naturalidad. El indicador responde al nivel de tu voz.",
                         symbol: "waveform", tone: .neutral)
        case .followingUp:
            return .init(kind: .followingUp, title: "Puedes continuar", compactTitle: "Puedes continuar",
                         detail: "La escucha de continuidad está abierta durante unos segundos.",
                         symbol: "ear.badge.waveform", tone: .neutral)
        case .submitting:
            return .init(kind: .submitting, title: "Enviando la solicitud", compactTitle: "Enviando",
                         detail: "Preparando el turno. Todavía no hay un resultado confirmado.",
                         symbol: "arrow.up.circle", tone: .active)
        case .processing:
            return .init(kind: .processing, title: "Procesando la solicitud", compactTitle: "Procesando",
                         detail: "Jarvis está trabajando. Las acciones que lo requieran pedirán tu autorización.",
                         symbol: "brain.head.profile", tone: .active)
        case .speaking:
            return .init(kind: .speaking, title: "Respondiendo", compactTitle: "Respondiendo",
                         detail: "Reproduciendo la respuesta del turno.", symbol: "speaker.wave.2", tone: .neutral)
        case .idle, .completed, .failed, .awaitingAuthorization: break
        }
        let jobs = activity.values.reduce(0) { total, count in total + min(max(count, 0), 999) }
        if jobs > 0 {
            return .init(kind: .working, title: "Agentes en actividad", compactTitle: "Agentes trabajando",
                         detail: "\(jobs) \(jobs == 1 ? "tarea activa" : "tareas activas"). El resultado aún no está confirmado.",
                         symbol: "circle.hexagongrid.fill", tone: .active)
        }
        if voice == .completed {
            return .init(kind: .completed, title: "Turno finalizado", compactTitle: "Turno finalizado",
                         detail: "La respuesta terminó. Consulta su contenido para conocer el resultado de la solicitud.",
                         symbol: "checkmark.circle", tone: .success)
        }
        if ["job_cancelled", "job_superseded", "cancelled"].contains(failureCode) {
            return .init(kind: .cancelled, title: "Turno cancelado", compactTitle: "Turno cancelado",
                         detail: "El turno se detuvo. Esto no revierte las acciones que ya se hayan realizado.",
                         symbol: "stop.circle", tone: .neutral)
        }
        if ambientListening {
            return .init(kind: .ambient, title: "Esperando «Jarvis»", compactTitle: "Escucha ambiental",
                         detail: "La activación por voz está escuchando en este Mac.",
                         symbol: "ear", tone: .neutral)
        }
        return .init(kind: .idle, title: "En espera", compactTitle: "En espera",
                     detail: "Inicia un turno desde el menú de Jarvis. La escucha ambiental no está activa.",
                     symbol: "waveform", tone: .neutral)
    }

    private static func failureDetail(for code: String?) -> String {
        switch code {
        case "confirmation_expired", "confirmation_consumption_failed":
            "La aprobación caducó o no pudo validarse. Revisa la solicitud antes de iniciarla de nuevo."
        case "job_status_unavailable", "job_timeout", "swarm_execution_timeout":
            "No se confirmó el resultado final. Comprueba si hubo cambios antes de repetir la acción."
        case "tcc_permission_denied", "microphonePermission", "speechPermission", "voice_preflight_unavailable":
            "Revisa los permisos de macOS y el estado del servicio desde el menú de Jarvis."
        case "noAudibleInput", "noFinalTranscript":
            "No se obtuvo una transcripción. Acércate al micrófono e inicia un turno nuevo."
        case "brain_unavailable", "remote_provider_unavailable":
            "La ruta de inferencia no respondió. Revisa su disponibilidad antes de volver a intentarlo."
        case "owner_verification_required", "voice_conversation_owner_required", "speakerIdentityUnverified":
            "No se pudo verificar tu identidad de voz. Revisa la configuración de identidad."
        case "secret_material_rejected":
            "La solicitud parecía contener una credencial. No incluyas claves ni contraseñas."
        case "empty_agent_response", "job_result_invalid", "job_stream_invalid":
            "La respuesta llegó incompleta. Revisa posibles cambios antes de repetir una acción."
        default:
            "No se pudo completar la solicitud. Revisa el resultado antes de volver a intentarlo."
        }
    }
}
