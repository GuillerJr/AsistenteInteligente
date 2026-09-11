import AegisAudioCore
import SwiftUI

enum HUDStyle {
    static let cornerRadius: CGFloat = 24

    static func accent(for tone: AssistantPresentation.Tone) -> Color {
        switch tone {
        case .neutral, .active: Color(red: 0.62, green: 0.79, blue: 0.94)
        case .warning: Color(red: 0.94, green: 0.74, blue: 0.43)
        case .failure: Color(red: 0.98, green: 0.57, blue: 0.55)
        case .success: Color(red: 0.55, green: 0.84, blue: 0.72)
        }
    }
}

struct HUDSurface: View {
    let opaque: Bool

    var body: some View {
        ZStack {
            if !opaque { Rectangle().fill(.ultraThinMaterial) }
            LinearGradient(
                colors: [Color(red: 0.115, green: 0.13, blue: 0.15),
                         Color(red: 0.055, green: 0.065, blue: 0.08)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
            .opacity(opaque ? 1 : 0.94)
        }
        .accessibilityHidden(true)
    }
}

struct HUDHeader: View {
    let close: () -> Void
    let highContrast: Bool
    @State private var hoveringClose = false

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "waveform")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.white.opacity(0.85))
                .frame(width: 32, height: 32)
                .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            Text("Jarvis")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.95))
            Text("ASISTENTE")
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1.5)
                .foregroundStyle(.white.opacity(highContrast ? 0.85 : 0.5))
                .padding(.leading, 4)
            Spacer(minLength: 8)
            Button(action: close) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(hoveringClose || highContrast ? 0.95 : 0.6))
                    .frame(width: 28, height: 28)
                    .contentShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .background(.white.opacity(hoveringClose ? 0.12 : 0.04),
                        in: RoundedRectangle(cornerRadius: 8))
            .onHover { hoveringClose = $0 }
            .keyboardShortcut(.cancelAction)
            .help("Cerrar HUD (Esc). No cancela el turno.")
            .accessibilityLabel("Cerrar HUD")
            .accessibilityHint("Cierra el panel sin cancelar la tarea en curso.")
        }
        .padding(.horizontal, 22)
        .frame(height: 68)
    }
}

struct HUDStatusMessage: View {
    let status: AssistantPresentation

    var body: some View {
        VStack(spacing: 10) {
            Text(status.title)
                .font(.system(size: 22, weight: .semibold))
                .tracking(-0.45)
                .foregroundStyle(.white.opacity(0.96))
                .fixedSize(horizontal: false, vertical: true)
            Text(status.detail)
                .font(.system(size: 13))
                .lineSpacing(3)
                .foregroundStyle(.white.opacity(0.7))
                .fixedSize(horizontal: false, vertical: true)
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: 360)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.accessibilityDescription)
    }
}

struct HUDActivitySummary: View {
    let activity: [IPCSwarmAgentRole: Int]
    let accent: Color

    private var activeRoles: [IPCSwarmAgentRole] {
        SwarmRoleVisuals.orderedRoles.filter { activity[$0, default: 0] > 0 }
    }

    var body: some View {
        if !activeRoles.isEmpty {
            Text(activeRoles.map(title).joined(separator: "  ·  "))
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(accent)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(accent.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityLabel("Agentes activos: " + activeRoles.map(title).joined(separator: ", "))
        }
    }

    private func title(_ role: IPCSwarmAgentRole) -> String {
        switch role {
        case .router: "Enrutamiento"
        case .planner: "Planificación"
        case .criticalReasoner: "Razonamiento"
        case .codeSecurity: "Código y seguridad"
        case .vision: "Visión"
        case .omni: "Coordinación"
        case .synthesizer: "Síntesis"
        }
    }
}

struct HUDConnectionFooter: View {
    let daemon: DaemonConnectionState
    let security: SecurityMonitorState

    private var verified: Bool { daemon == .online && security == .intact }
    private var title: String {
        if daemon == .securityFailure || security == .compromised { return "Servicio bloqueado" }
        if daemon == .offline { return "Sin conexión al servicio" }
        if security == .unavailable { return "Servicio sin verificar" }
        return verified ? "Servicio conectado" : "Verificando servicio"
    }

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: verified ? "checkmark.shield" : "shield.lefthalf.filled")
                .font(.system(size: 11))
            Text(title).font(.system(size: 11))
            Spacer(minLength: 8)
            Text("esc")
                .font(.system(size: 10, design: .monospaced))
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(.white.opacity(0.16)))
            Text("Cerrar").font(.system(size: 11))
        }
        .foregroundStyle(.white.opacity(0.6))
        .padding(.horizontal, 22)
        .frame(height: 56)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title + ". Escape cierra el HUD sin cancelar la tarea.")
    }
}

struct HUDActionStyle: ButtonStyle {
    let accent: Color
    var prominent = false
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.colorSchemeContrast) private var contrast

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(prominent ? Color(red: 0.09, green: 0.1, blue: 0.12) : .white.opacity(0.92))
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .background {
                RoundedRectangle(cornerRadius: 10)
                    .fill(prominent ? accent : .white.opacity(configuration.isPressed ? 0.14 : 0.06))
            }
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(.white.opacity(contrast == .increased ? 0.6 : 0.1), lineWidth: 1)
            }
            .opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : 0.45)
            .contentShape(RoundedRectangle(cornerRadius: 10))
    }
}
