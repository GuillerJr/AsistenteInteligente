import AegisAudioCore
import SwiftUI

struct HUDView: View {
    let model: MenuBarModel
    let close: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Circle()
                .stroke(accentColor.opacity(0.1), lineWidth: 0.7)
                .frame(width: 486, height: 486)
            Circle()
                .stroke(
                    accentColor.opacity(0.12),
                    style: StrokeStyle(lineWidth: 0.65, dash: [2, 9])
                )
                .frame(width: 424, height: 424)

            NodeSphereView(
                activity: model.hudActivity,
                voiceLevel: model.voiceActivityLevel,
                listeningPulse: model.isInterruptingSpeech
                    || model.voiceState == .listening
                    || model.voiceState == .followingUp,
                interrupting: model.isInterruptingSpeech,
                reduceMotion: reduceMotion
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityHidden(true)

            VStack(spacing: 0) {
                HStack(spacing: 9) {
                    Circle()
                        .fill(accentColor)
                        .frame(width: 5, height: 5)
                        .shadow(color: accentColor, radius: 4)
                    Text("JARVIS / SWARM")
                        .font(.caption.monospaced().weight(.semibold))
                        .tracking(2)
                    Spacer(minLength: 12)
                    Text(linkTitle)
                        .font(.system(size: 7.5, weight: .semibold, design: .monospaced))
                        .tracking(1)
                        .opacity(0.62)
                    Button(action: close) {
                        Image(systemName: "xmark.circle")
                            .font(.title3.weight(.medium))
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .keyboardShortcut(.cancelAction)
                    .help("Cerrar HUD (Esc)")
                    .accessibilityLabel("Cerrar HUD")
                }
                .padding(18)

                Spacer(minLength: 0)

                VStack(spacing: 4) {
                    Text(status)
                        .font(.caption2.monospaced().weight(.semibold))
                        .tracking(1.45)
                    Text(detail)
                        .font(.system(size: 7.5, weight: .medium, design: .monospaced))
                        .tracking(0.85)
                        .opacity(0.58)
                }
                .contentTransition(.opacity)
                .padding(.bottom, 18)
            }
            .foregroundStyle(accentColor.opacity(0.92))
            .shadow(color: .black.opacity(0.22), radius: 1, y: 1)
        }
        .frame(width: 560, height: 560)
        .background(Color.clear)
        .animation(stateAnimation, value: status)
        .animation(stateAnimation, value: activeRoles)
    }

    private var activeRoles: [IPCSwarmAgentRole] {
        SwarmRoleVisuals.orderedRoles.filter { model.hudActivity[$0, default: 0] > 0 }
    }

    private var status: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "AUDITORÍA COMPROMETIDA"
        }
        if model.daemonState != .online {
            return "DAEMON NO DISPONIBLE"
        }
        switch model.providerState {
        case .missing: return "NVIDIA NO CONFIGURADA"
        case .unavailable: return "NVIDIA NO VERIFICABLE"
        case .unknown, .checking: return "VERIFICANDO NVIDIA"
        case .configured: break
        }
        switch model.voiceState {
        case .listening: return "ESCUCHA ACTIVA"
        case .followingUp: return "ESCUCHA DE CONTINUIDAD"
        case .submitting: return "ENLAZANDO"
        case .processing: return "PROCESANDO"
        case .awaitingAuthorization: return "TOUCH ID O VOZ"
        case .speaking: return "RESPONDIENDO"
        case .completed: return "LISTO"
        case .failed: return "REVISAR SISTEMA"
        case .idle:
            return activeRoles.isEmpty
                ? "EN REPOSO"
                : activeRoles.map { SwarmRoleVisuals.title(for: $0) }.joined(separator: "  /  ")
        }
    }

    private var detail: String {
        if model.voiceState == .awaitingAuthorization {
            return "USA TOUCH ID O DI ‘APROBADO’"
        }
        guard !activeRoles.isEmpty else {
            guard model.daemonState == .online else {
                return "CORE LINK \(model.daemonState.title.uppercased())"
            }
            switch model.providerState {
            case .missing: return "AÑADE LA API KEY EN KEYCHAIN"
            case .unavailable: return "KEYCHAIN NO DISPONIBLE"
            case .unknown, .checking: return "SONDEO LOCAL DE CREDENCIAL"
            case .configured: return "7 AGENTES DISPONIBLES"
            }
        }
        let jobs = activeRoles.reduce(0) { $0 + model.hudActivity[$1, default: 0] }
        return "\(jobs) \(jobs == 1 ? "TAREA ACTIVA" : "TAREAS ACTIVAS")"
    }

    private var accentColor: Color {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return .red
        }
        if model.daemonState != .online || model.securityState == .unavailable {
            return .orange
        }
        if model.providerState == .missing || model.providerState == .unavailable {
            return .orange
        }
        if let primary = activeRoles.first {
            return SwarmRoleVisuals.color(for: primary)
        }
        switch model.voiceState {
        case .awaitingAuthorization: return .orange
        case .failed: return .red
        case .processing, .submitting: return .purple
        case .completed: return .green
        case .idle, .listening, .followingUp, .speaking: return .cyan
        }
    }

    private var linkTitle: String {
        model.daemonState == .online ? "CORE LINKED" : "CORE OFFLINE"
    }

    private var stateAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.1) : .easeOut(duration: 0.22)
    }
}
