import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    let toggle: () -> Void
    let startVoiceTurn: () -> Void
    let configureVoice: () -> Void
    let reviewApproval: () -> Void
    let showHUD: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var closeHovering = false
    @State private var primaryHovering = false
    @State private var hudHovering = false

    private var notchWidth: CGFloat { presentation.notchWidth }
    private var notchHeight: CGFloat { presentation.notchHeight }
    private var wingWidth: CGFloat { presentation.wingWidth }
    private var expanded: Bool { presentation.expanded }

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
                .frame(width: wingWidth)
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
                .frame(width: wingWidth)
            }
            .frame(width: notchWidth + (wingWidth * 2), height: notchHeight)

            neuralUnderline
                .frame(height: 8, alignment: .top)

            if expanded {
                expandedConsole
                    .frame(height: 164)
                    .transition(consoleTransition)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(alignment: .top) {
            notchSurface
        }
        .animation(.smooth(duration: 0.16), value: model.voiceActivityLevel)
        .animation(.smooth(duration: 0.24), value: model.voiceState)
        .animation(expansionAnimation, value: expanded)
        .onExitCommand {
            if expanded {
                toggle()
            }
        }
    }

    private var notchSurface: some View {
        ZStack(alignment: .top) {
            if expanded {
                expandedSurface
                    .transition(
                        .opacity.combined(with: .scale(scale: 0.94, anchor: .top))
                    )
            }
            notchStem(height: notchHeight + (expanded ? 10 : 8))
        }
        .shadow(
            color: statusColor.opacity(expanded ? 0.22 : 0.08),
            radius: expanded ? 20 : 12,
            y: expanded ? 7 : 3
        )
    }

    private var expandedSurface: some View {
        UnevenRoundedRectangle(
            topLeadingRadius: 18,
            bottomLeadingRadius: 22,
            bottomTrailingRadius: 22,
            topTrailingRadius: 18
        )
        .fill(surfaceGradient)
        .overlay {
            UnevenRoundedRectangle(
                topLeadingRadius: 18,
                bottomLeadingRadius: 22,
                bottomTrailingRadius: 22,
                topTrailingRadius: 18
            )
            .stroke(surfaceBorder, lineWidth: 1)
        }
        .overlay(alignment: .top) {
            LinearGradient(
                colors: [.clear, statusColor.opacity(0.34), .clear],
                startPoint: .leading,
                endPoint: .trailing
            )
            .frame(height: 1)
            .padding(.horizontal, 22)
        }
        .padding(.top, notchHeight - 1)
    }

    private func notchStem(height: CGFloat) -> some View {
        UnevenRoundedRectangle(
            bottomLeadingRadius: expanded ? 9 : 7,
            bottomTrailingRadius: expanded ? 9 : 7
        )
        .fill(surfaceGradient)
        .frame(width: notchWidth, height: height)
        .overlay {
            UnevenRoundedRectangle(
                bottomLeadingRadius: expanded ? 9 : 7,
                bottomTrailingRadius: expanded ? 9 : 7
            )
            .stroke(surfaceBorder, lineWidth: 1)
        }
    }

    private var surfaceGradient: LinearGradient {
        LinearGradient(
            colors: [.black, Color(red: 0.018, green: 0.027, blue: 0.045)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    private var surfaceBorder: LinearGradient {
        LinearGradient(
            colors: [
                .clear,
                statusColor.opacity(expanded ? 0.48 : 0.2),
                Color.purple.opacity(expanded ? 0.35 : 0.12),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
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
                        .font(.system(size: 8, weight: .bold, design: .monospaced))
                        .tracking(0.9)
                        .foregroundStyle(statusColor.opacity(0.95))
                        .lineLimit(1)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(statusColor.opacity(0.1), in: Capsule())
                        .overlay {
                            Capsule().stroke(statusColor.opacity(0.22), lineWidth: 0.5)
                        }
                        .contentTransition(.opacity)

                    Label(securityTitle, systemImage: securitySymbol)
                        .font(.system(size: 8, weight: .medium, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.4))
                }

                Spacer(minLength: 8)

                Button(action: toggle) {
                    Image(systemName: "chevron.up")
                        .font(.system(size: 10, weight: .bold))
                        .frame(width: 26, height: 26)
                        .background(
                            closeHovering ? statusColor.opacity(0.14) : .white.opacity(0.055),
                            in: Circle()
                        )
                        .overlay {
                            Circle().stroke(.white.opacity(0.08), lineWidth: 1)
                        }
                }
                .buttonStyle(.plain)
                .foregroundStyle(closeHovering ? statusColor : .white.opacity(0.65))
                .scaleEffect(closeHovering ? 1.06 : 1)
                .animation(.easeOut(duration: 0.14), value: closeHovering)
                .onHover { closeHovering = $0 }
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
                Button(action: primaryAction) {
                    HStack(spacing: 9) {
                        Image(systemName: primaryActionSymbol)
                            .font(.system(size: 20, weight: .medium))
                            .contentTransition(.opacity)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(primaryActionTitle)
                                .font(.system(size: 10, weight: .bold, design: .rounded))
                                .tracking(0.8)
                                .contentTransition(.opacity)
                            Text(primaryActionSubtitle)
                                .font(.system(size: 8, weight: .medium))
                                .foregroundStyle(.white.opacity(0.48))
                        }
                        Spacer(minLength: 0)
                    }
                }
                .buttonStyle(
                    NotchActionButtonStyle(
                        color: primaryActionColor,
                        emphasized: true,
                        hovering: primaryHovering
                    )
                )
                .onHover { primaryHovering = $0 }
                .disabled(!primaryActionAvailable)
                .opacity(primaryActionAvailable ? 1 : 0.38)
                .help(primaryActionHelp)

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
                .buttonStyle(
                    NotchActionButtonStyle(
                        color: .purple,
                        emphasized: false,
                        hovering: hudHovering
                    )
                )
                .onHover { hudHovering = $0 }
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

    private var expansionAnimation: Animation {
        reduceMotion
            ? .easeOut(duration: 0.12)
            : .spring(response: 0.34, dampingFraction: 0.86, blendDuration: 0.08)
    }

    private var consoleTransition: AnyTransition {
        if reduceMotion {
            return .opacity
        }
        return .asymmetric(
            insertion: .opacity.combined(with: .move(edge: .top)),
            removal: .opacity.combined(with: .scale(scale: 0.97, anchor: .top))
        )
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

    private var primaryActionAvailable: Bool {
        model.pendingApproval != nil
            || model.canStartVoiceTurn
            || voicePermissionPending
            || voicePermissionBlocked
    }

    private var primaryActionTitle: String {
        if model.pendingApproval != nil { return "REVISAR ACCIÓN" }
        if model.canStartVoiceTurn { return "INICIAR VOZ" }
        if voicePermissionBlocked { return "AJUSTAR VOZ" }
        if voicePermissionPending { return "CONFIGURAR VOZ" }
        return model.voiceState.isBusy ? "JARVIS OCUPADO" : "VOZ NO DISPONIBLE"
    }

    private var primaryActionSubtitle: String {
        if model.pendingApproval != nil { return "Confirmación de un solo uso" }
        if model.canStartVoiceTurn { return "Procesamiento local" }
        if voicePermissionBlocked { return "Privacidad de macOS" }
        if voicePermissionPending { return "Micrófono y Speech" }
        return model.voiceState.isBusy ? "Procesando solicitud" : "Revisar daemon y seguridad"
    }

    private var primaryActionSymbol: String {
        if model.pendingApproval != nil { return "hand.raised.fill" }
        if model.canStartVoiceTurn { return "waveform.circle.fill" }
        if voicePermissionBlocked { return "gearshape.fill" }
        if voicePermissionPending { return "mic.badge.plus" }
        return "waveform.slash"
    }

    private var primaryActionColor: Color {
        model.pendingApproval == nil ? .cyan : .orange
    }

    private var primaryActionHelp: String {
        if model.pendingApproval != nil { return "Revisar aprobación pendiente" }
        if model.canStartVoiceTurn { return "Iniciar turno de voz" }
        if voicePermissionBlocked { return "Abrir ajustes de privacidad" }
        if voicePermissionPending { return "Solicitar permisos de voz" }
        return "Voz no disponible"
    }

    private func primaryAction() {
        if model.pendingApproval != nil {
            reviewApproval()
        } else if model.canStartVoiceTurn {
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
            HStack(spacing: 2) {
                ForEach(0..<3, id: \.self) { index in
                    Capsule()
                        .fill(color.opacity(opacity(for: index)))
                        .frame(width: width(for: index), height: index == 2 ? 3 : 2)
                }
                Circle()
                    .fill(color.opacity(0.55 + (Double(energy) * 0.4)))
                    .frame(width: 3.5, height: 3.5)
                    .shadow(color: color.opacity(0.7), radius: hovering ? 5 : 3)
            }
            .scaleEffect(x: mirrored ? -1 : 1)
            .frame(width: 24, height: 18)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .scaleEffect(hovering ? 1.04 : 1)
        .animation(.easeOut(duration: 0.14), value: hovering)
        .onHover { hovering = $0 }
        .accessibilityLabel(expanded ? "Contraer Jarvis" : "Abrir consola de Jarvis")
    }

    private func width(for index: Int) -> CGFloat {
        CGFloat(3 + (index * 2)) + (energy * 0.5)
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
        .animation(.spring(response: 0.24, dampingFraction: 0.82), value: energy)
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
    let hovering: Bool

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
                                color.opacity(backgroundOpacity(configuration.isPressed)),
                                Color.white.opacity(hovering ? 0.045 : 0.025),
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(
                                color.opacity(borderOpacity(configuration.isPressed)),
                                lineWidth: hovering ? 1.2 : 1
                            )
                    }
            }
            .shadow(
                color: color.opacity(hovering && !configuration.isPressed ? 0.18 : 0),
                radius: 8,
                y: 2
            )
            .scaleEffect(configuration.isPressed ? 0.975 : hovering ? 1.012 : 1)
            .animation(.easeOut(duration: 0.14), value: configuration.isPressed)
            .animation(.easeOut(duration: 0.16), value: hovering)
    }

    private func backgroundOpacity(_ pressed: Bool) -> Double {
        if pressed { return 0.16 }
        return hovering ? 0.16 : 0.11
    }

    private func borderOpacity(_ pressed: Bool) -> Double {
        if pressed { return emphasized ? 0.34 : 0.2 }
        if hovering { return emphasized ? 0.62 : 0.4 }
        return emphasized ? 0.42 : 0.22
    }
}
