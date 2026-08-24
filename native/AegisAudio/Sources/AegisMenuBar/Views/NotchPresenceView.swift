import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let notchWidth: CGFloat
    let notchHeight: CGFloat
    let expanded: Bool
    let toggle: () -> Void
    let startVoiceTurn: () -> Void
    let configureVoice: () -> Void
    let showHUD: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                NotchWingButton(
                    color: statusColor,
                    energy: visualEnergy,
                    expanded: expanded,
                    mirrored: false,
                    action: toggle
                )
                Color.clear
                    .frame(width: notchWidth)
                    .allowsHitTesting(false)
                NotchWingButton(
                    color: statusColor,
                    energy: visualEnergy,
                    expanded: expanded,
                    mirrored: true,
                    action: toggle
                )
            }
            .frame(height: notchHeight)

            neuralUnderline
                .frame(height: 8, alignment: .top)

            if expanded {
                expandedConsole
                    .frame(height: 164)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(alignment: .top) {
            notchSurface
        }
        .animation(.easeOut(duration: 0.12), value: model.voiceActivityLevel)
        .animation(.easeInOut(duration: 0.22), value: model.voiceState)
        .animation(.easeInOut(duration: 0.22), value: expanded)
        .onExitCommand {
            if expanded {
                toggle()
            }
        }
    }

    private var notchSurface: some View {
        UnevenRoundedRectangle(
            bottomLeadingRadius: expanded ? 22 : 12,
            bottomTrailingRadius: expanded ? 22 : 12
        )
        .fill(
            LinearGradient(
                colors: [.black, Color(red: 0.018, green: 0.027, blue: 0.045)],
                startPoint: .top,
                endPoint: .bottom
            )
        )
        .overlay {
            UnevenRoundedRectangle(
                bottomLeadingRadius: expanded ? 22 : 12,
                bottomTrailingRadius: expanded ? 22 : 12
            )
            .stroke(
                LinearGradient(
                    colors: [
                        .clear,
                        statusColor.opacity(expanded ? 0.48 : 0.2),
                        Color.purple.opacity(expanded ? 0.35 : 0.12),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                ),
                lineWidth: 1
            )
        }
        .shadow(color: statusColor.opacity(expanded ? 0.18 : 0.08), radius: 16, y: 5)
    }

    private var neuralUnderline: some View {
        ZStack {
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [.clear, statusColor.opacity(statusOpacity), .clear],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(width: underlineWidth, height: 2)
                .shadow(color: statusColor.opacity(glowOpacity), radius: 5)

            Circle()
                .fill(statusColor)
                .frame(width: 3, height: 3)
                .shadow(color: statusColor, radius: 4)
        }
    }

    private var expandedConsole: some View {
        VStack(spacing: 11) {
            HStack(spacing: 12) {
                NeuralCoreView(
                    color: statusColor,
                    symbol: statusSymbol,
                    energy: visualEnergy
                )

                VStack(alignment: .leading, spacing: 3) {
                    Text("JARVIS")
                        .font(.system(size: 14, weight: .semibold, design: .rounded))
                        .tracking(2.4)
                        .foregroundStyle(.white)

                    Text(statusTitle.uppercased())
                        .font(.caption2.monospaced().weight(.semibold))
                        .tracking(1.1)
                        .foregroundStyle(statusColor.opacity(0.9))
                        .lineLimit(1)

                    Label(securityTitle, systemImage: securitySymbol)
                        .font(.system(size: 8, weight: .medium, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.4))
                }

                Spacer(minLength: 8)

                Button(action: toggle) {
                    Image(systemName: "chevron.up")
                        .font(.system(size: 10, weight: .bold))
                        .frame(width: 26, height: 26)
                        .background(.white.opacity(0.055), in: Circle())
                        .overlay {
                            Circle().stroke(.white.opacity(0.08), lineWidth: 1)
                        }
                }
                .buttonStyle(.plain)
                .foregroundStyle(.white.opacity(0.65))
                .accessibilityLabel("Contraer Jarvis")
            }

            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [.clear, statusColor.opacity(0.28), .clear],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(height: 1)

            HStack(spacing: 10) {
                Button(action: primaryVoiceAction) {
                    HStack(spacing: 9) {
                        Image(systemName: primaryVoiceSymbol)
                            .font(.system(size: 20, weight: .medium))
                        VStack(alignment: .leading, spacing: 1) {
                            Text(primaryVoiceTitle)
                                .font(.system(size: 10, weight: .bold, design: .rounded))
                                .tracking(0.8)
                            Text(primaryVoiceSubtitle)
                                .font(.system(size: 8, weight: .medium))
                                .foregroundStyle(.white.opacity(0.48))
                        }
                        Spacer(minLength: 0)
                    }
                }
                .buttonStyle(NotchActionButtonStyle(color: .cyan, emphasized: true))
                .disabled(!primaryVoiceActionAvailable)
                .opacity(primaryVoiceActionAvailable ? 1 : 0.38)
                .help(primaryVoiceHelp)

                Button(action: showHUD) {
                    VStack(spacing: 3) {
                        Image(systemName: "circle.hexagongrid.fill")
                            .font(.system(size: 17, weight: .medium))
                        Text("HUD")
                            .font(.system(size: 8, weight: .bold, design: .rounded))
                            .tracking(0.8)
                    }
                    .frame(width: 46)
                }
                .buttonStyle(NotchActionButtonStyle(color: .purple, emphasized: false))
                .help("Abrir esfera táctica")
            }
            .frame(height: 48)

            HStack(spacing: 7) {
                NeuralStatusDots(color: statusColor, active: model.voiceState != .idle)
                Text("CORE LOCAL")
                Circle().frame(width: 2, height: 2)
                Text("APPLE SILICON")
                Circle().frame(width: 2, height: 2)
                Text("ARM64")
                Spacer(minLength: 0)
            }
            .font(.system(size: 7, weight: .semibold, design: .monospaced))
            .tracking(0.7)
            .foregroundStyle(.white.opacity(0.3))
        }
        .padding(.horizontal, 16)
        .padding(.top, 9)
        .padding(.bottom, 12)
    }

    private var statusTitle: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "Auditoría comprometida"
        }
        if model.daemonState != .online {
            return "Daemon no disponible"
        }
        return model.voiceState.title
    }

    private var statusSymbol: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "exclamationmark.shield.fill"
        }
        switch model.voiceState {
        case .idle, .completed:
            return "sparkles"
        case .listening:
            return "waveform"
        case .submitting:
            return "arrow.up"
        case .processing:
            return "brain.head.profile"
        case .awaitingApproval:
            return "hand.raised.fill"
        case .speaking:
            return "speaker.wave.2.fill"
        case .failed:
            return "exclamationmark.triangle.fill"
        }
    }

    private var securityTitle: String {
        model.securityState == .intact ? "SISTEMA ÍNTEGRO" : "SEGURIDAD: \(model.securityState.title.uppercased())"
    }

    private var voicePermissionPending: Bool {
        model.microphonePermission == .notDetermined || model.speechPermission == .notDetermined
    }

    private var voicePermissionBlocked: Bool {
        [.denied, .restricted].contains(model.microphonePermission)
            || [.denied, .restricted].contains(model.speechPermission)
    }

    private var primaryVoiceActionAvailable: Bool {
        model.canStartVoiceTurn || voicePermissionPending || voicePermissionBlocked
    }

    private var primaryVoiceTitle: String {
        if model.canStartVoiceTurn { return "INICIAR VOZ" }
        if voicePermissionBlocked { return "AJUSTAR VOZ" }
        if voicePermissionPending { return "CONFIGURAR VOZ" }
        return model.voiceState.isBusy ? "JARVIS OCUPADO" : "VOZ NO DISPONIBLE"
    }

    private var primaryVoiceSubtitle: String {
        if model.canStartVoiceTurn { return "Procesamiento local" }
        if voicePermissionBlocked { return "Privacidad de macOS" }
        if voicePermissionPending { return "Micrófono y Speech" }
        return model.voiceState.isBusy ? "Procesando solicitud" : "Revisar daemon y seguridad"
    }

    private var primaryVoiceSymbol: String {
        if model.canStartVoiceTurn { return "waveform.circle.fill" }
        if voicePermissionBlocked { return "gearshape.fill" }
        if voicePermissionPending { return "mic.badge.plus" }
        return "waveform.slash"
    }

    private var primaryVoiceHelp: String {
        if model.canStartVoiceTurn { return "Iniciar turno de voz" }
        if voicePermissionBlocked { return "Abrir ajustes de privacidad" }
        if voicePermissionPending { return "Solicitar permisos de voz" }
        return "Voz no disponible"
    }

    private func primaryVoiceAction() {
        if model.canStartVoiceTurn {
            startVoiceTurn()
        } else {
            configureVoice()
        }
    }

    private var securitySymbol: String {
        model.securityState == .intact ? "checkmark.shield.fill" : "shield.slash.fill"
    }

    private var statusColor: Color {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return .red
        }
        if model.daemonState == .offline || model.securityState == .unavailable {
            return .orange
        }
        switch model.voiceState {
        case .awaitingApproval:
            return .orange
        case .failed:
            return .red
        case .completed:
            return .green
        case .processing, .submitting:
            return .purple
        case .idle, .listening, .speaking:
            return .cyan
        }
    }

    private var visualEnergy: CGFloat {
        switch model.voiceState {
        case .listening:
            return max(0.25, CGFloat(model.voiceActivityLevel))
        case .submitting, .processing, .awaitingApproval, .speaking:
            return 0.72
        case .failed:
            return 0.48
        case .idle, .completed:
            return 0.18
        }
    }

    private var statusOpacity: Double {
        model.voiceState == .idle ? 0.42 : 0.92
    }

    private var glowOpacity: Double {
        model.voiceState == .idle ? 0.18 : 0.72
    }

    private var underlineWidth: CGFloat {
        32 + (visualEnergy * 42)
    }
}

private struct NotchWingButton: View {
    let color: Color
    let energy: CGFloat
    let expanded: Bool
    let mirrored: Bool
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 3) {
                ForEach(0..<3, id: \.self) { index in
                    Capsule()
                        .fill(color.opacity(opacity(for: index)))
                        .frame(width: width(for: index), height: index == 2 ? 3 : 2)
                }
                Circle()
                    .fill(color.opacity(0.55 + (Double(energy) * 0.4)))
                    .frame(width: 4, height: 4)
                    .shadow(color: color.opacity(0.7), radius: hovering ? 5 : 3)
            }
            .scaleEffect(x: mirrored ? -1 : 1)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .scaleEffect(hovering ? 1.08 : 1)
        .animation(.easeOut(duration: 0.14), value: hovering)
        .onHover { hovering = $0 }
        .accessibilityLabel(expanded ? "Contraer Jarvis" : "Abrir consola de Jarvis")
    }

    private func width(for index: Int) -> CGFloat {
        CGFloat(4 + (index * 3)) + (energy * CGFloat(index + 1) * 2)
    }

    private func opacity(for index: Int) -> Double {
        0.18 + (Double(index) * 0.12) + (Double(energy) * 0.42)
    }
}

private struct NeuralCoreView: View {
    let color: Color
    let symbol: String
    let energy: CGFloat

    var body: some View {
        ZStack {
            Circle()
                .stroke(color.opacity(0.14), lineWidth: 6)
            Circle()
                .trim(from: 0.08, to: 0.82)
                .stroke(
                    AngularGradient(
                        colors: [color, .purple, color.opacity(0.25), color],
                        center: .center
                    ),
                    style: StrokeStyle(lineWidth: 2, lineCap: .round)
                )
                .rotationEffect(.degrees(-35))
            Circle()
                .fill(
                    RadialGradient(
                        colors: [color.opacity(0.24), Color.white.opacity(0.025)],
                        center: .center,
                        startRadius: 0,
                        endRadius: 20
                    )
                )
                .padding(6)
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(color)
                .shadow(color: color.opacity(0.8), radius: 4)
        }
        .frame(width: 46, height: 46)
        .scaleEffect(1 + (energy * 0.035))
        .accessibilityHidden(true)
    }
}

private struct NeuralStatusDots: View {
    let color: Color
    let active: Bool

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(color.opacity(active ? 0.35 + (Double(index) * 0.2) : 0.22))
                    .frame(width: 3, height: 3)
            }
        }
        .accessibilityHidden(true)
    }
}

private struct NotchActionButtonStyle: ButtonStyle {
    let color: Color
    let emphasized: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.white.opacity(configuration.isPressed ? 0.68 : 0.92))
            .padding(.horizontal, emphasized ? 12 : 8)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [
                                color.opacity(configuration.isPressed ? 0.16 : 0.11),
                                Color.white.opacity(0.025),
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(color.opacity(emphasized ? 0.42 : 0.22), lineWidth: 1)
                    }
            }
            .scaleEffect(configuration.isPressed ? 0.975 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}
