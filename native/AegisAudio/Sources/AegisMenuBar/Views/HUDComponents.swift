import AegisAudioCore
import AppKit
import SwiftUI

enum HUDStyle {
    static let cornerRadius: CGFloat = 24

    static func accent(for tone: AssistantPresentation.Tone) -> Color {
        switch tone {
        case .neutral, .active: adaptive((0.62, 0.79, 0.94), (0.16, 0.38, 0.62))
        case .warning: adaptive((0.94, 0.74, 0.43), (0.55, 0.34, 0.06))
        case .failure: adaptive((0.98, 0.57, 0.55), (0.72, 0.20, 0.19))
        case .success: adaptive((0.55, 0.84, 0.72), (0.12, 0.43, 0.30))
        }
    }

    private static func adaptive(_ dark: (Double, Double, Double),
                                 _ light: (Double, Double, Double)) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let rgb = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: rgb.0, green: rgb.1, blue: rgb.2, alpha: 1)
        })
    }
}

struct HUDSurface: View {
    let opaque: Bool
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            if !opaque { Rectangle().fill(.ultraThinMaterial) }
            LinearGradient(
                colors: colorScheme == .dark
                    ? [Color(red: 0.115, green: 0.13, blue: 0.15),
                       Color(red: 0.055, green: 0.065, blue: 0.08)]
                    : [Color(red: 0.98, green: 0.985, blue: 0.995),
                       Color(red: 0.91, green: 0.935, blue: 0.96)],
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
                .foregroundStyle(Color.primary.opacity(0.85))
                .frame(width: 32, height: 32)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            Text("Jarvis")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Color.primary.opacity(0.95))
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
                .foregroundStyle(Color.primary.opacity(0.96))
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
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Color.primary.opacity(0.16)))
            Text("Cerrar").font(.system(size: 11))
        }
        .foregroundStyle(Color.primary.opacity(0.6))
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
