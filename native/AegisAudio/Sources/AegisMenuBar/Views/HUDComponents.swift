import AegisAudioCore
import AppKit
import SwiftUI

enum HUDStyle {
    static let cornerRadius: CGFloat = 24

    static func accent(for tone: AssistantPresentation.Tone) -> Color {
        switch tone {
        case .neutral, .active: JarvisTheme.clay
        case .warning: JarvisTheme.warning
        case .failure: JarvisTheme.failure
        case .success: JarvisTheme.success
        }
    }
}

struct HUDSurface: View {
    let opaque: Bool

    var body: some View {
        // Flat bone canvas in both modes; the warm palette needs no gradient or blur.
        Rectangle().fill(JarvisTheme.canvas).accessibilityHidden(true)
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
                .foregroundStyle(JarvisTheme.ink)
                .frame(width: 32, height: 32)
                .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            Text("Jarvis")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(JarvisTheme.ink)
            Text("ASISTENTE")
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1.5)
                .foregroundStyle(Color.primary.opacity(highContrast ? 0.85 : 0.5))
                .padding(.leading, 4)
            Spacer(minLength: 8)
            Button(action: close) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Color.primary.opacity(hoveringClose || highContrast ? 0.95 : 0.6))
                    .frame(width: 28, height: 28)
                    .contentShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .background(Color.primary.opacity(hoveringClose ? 0.12 : 0.04),
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
                .foregroundStyle(JarvisTheme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text(status.detail)
                .font(.system(size: 14))
                .lineSpacing(3)
                .foregroundStyle(JarvisInterfaceStyle.secondaryText)
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
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(JarvisTheme.hairline))
            Text("Cerrar").font(.system(size: 11))
        }
        .foregroundStyle(JarvisTheme.inkSecondary)
        .padding(.horizontal, 22)
        .frame(height: 56)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title + ". Escape cierra el HUD sin cancelar la tarea.")
    }
}

struct HUDActionStyle: ButtonStyle {
    let accent: Color
    var prominent = false
    func makeBody(configuration: Configuration) -> some View {
        JarvisButtonStyle(accent: accent, prominent: prominent).makeBody(configuration: configuration)
    }
}
