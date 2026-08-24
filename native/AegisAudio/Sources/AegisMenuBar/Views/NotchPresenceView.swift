import AegisAudioCore
import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var notchWidth: CGFloat { presentation.notchWidth }
    private var notchHeight: CGFloat { presentation.notchHeight }
    private var wingWidth: CGFloat { presentation.wingWidth }

    var body: some View {
        TimelineView(
            .animation(
                minimumInterval: reduceMotion ? 1 : refreshInterval,
                paused: reduceMotion
            )
        ) { timeline in
            presence(phase: timeline.date.timeIntervalSinceReferenceDate)
        }
        .allowsHitTesting(false)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Jarvis: \(statusTitle)")
    }

    private func presence(phase: TimeInterval) -> some View {
        VStack(spacing: 0) {
            topRail(phase: phase)
                .frame(height: notchHeight)

            ambientBody(phase: phase)
                .frame(height: 62, alignment: .top)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(alignment: .top) {
            notchStem
        }
    }

    private func topRail(phase: TimeInterval) -> some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            AmbientWing(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                mirrored: false
            )
            .frame(width: wingWidth)

            Color.clear
                .frame(width: notchWidth)

            AmbientWing(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                mirrored: true
            )
            .frame(width: wingWidth)
            Spacer(minLength: 0)
        }
    }

    private func ambientBody(phase: TimeInterval) -> some View {
        ZStack(alignment: .top) {
            NeuralMotes(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                active: prominent
            )
            .frame(width: min(notchWidth + 150, 304), height: 58)
            .offset(y: 1)

            neuralBridge(phase: phase)

            if prominent {
                activePresence(phase: phase)
                    .transition(
                        .opacity.combined(with: .scale(scale: 0.88, anchor: .top))
                    )
            } else {
                restingPresence(phase: phase)
                    .transition(.opacity.combined(with: .scale(scale: 0.92, anchor: .top)))
            }
        }
        .animation(stateAnimation, value: prominent)
        .animation(.easeOut(duration: 0.2), value: model.voiceState)
    }

    private func neuralBridge(phase: TimeInterval) -> some View {
        let breath = idleBreath(phase)
        let scan = CGFloat(sin(phase * (prominent ? 2.4 : 1.15)))
        return ZStack {
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [.clear, statusColor.opacity(0.42 + (breath * 0.2)), .clear],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(width: 70 + (breath * 22), height: 1.5)
                .shadow(color: statusColor.opacity(0.42), radius: 5)

            Circle()
                .fill(statusColor)
                .frame(width: 3, height: 3)
                .shadow(color: statusColor, radius: 4 + (breath * 2))
                .offset(x: scan * (32 + (visualEnergy * 12)))

            Circle()
                .fill(.white.opacity(0.75))
                .frame(width: 1.5, height: 1.5)
                .shadow(color: statusColor, radius: 3)
                .offset(x: -scan * 22)
        }
        .frame(height: 8, alignment: .top)
    }

    private func restingPresence(phase: TimeInterval) -> some View {
        let breath = idleBreath(phase)
        return HStack(spacing: 8) {
            LivingIris(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                awake: wakeWordAwake,
                motionEnabled: !reduceMotion
            )
            .frame(width: 36, height: 24)

            VStack(alignment: .leading, spacing: 1) {
                Text("JARVIS")
                    .font(.system(size: 9, weight: .semibold, design: .rounded))
                    .tracking(1.5)
                    .foregroundStyle(.white.opacity(0.9))
                Text(restingSubtitle)
                    .font(.system(size: 6.5, weight: .bold, design: .monospaced))
                    .tracking(0.55)
                    .foregroundStyle(statusColor.opacity(0.72))
            }

            NeuralWave(
                color: statusColor,
                energy: max(0.12, visualEnergy * 0.55),
                phase: phase
            )
            .frame(width: 28, height: 18)
        }
        .padding(.horizontal, 11)
        .frame(height: 32)
        .background(surfaceGradient, in: Capsule())
        .background {
            Capsule()
                .fill(statusColor.opacity(0.08 + (breath * 0.05)))
                .blur(radius: 10)
                .scaleEffect(x: 1.08 + (breath * 0.03), y: 0.72)
        }
        .overlay {
            Capsule().stroke(statusColor.opacity(0.18 + (breath * 0.08)), lineWidth: 1)
        }
        .shadow(color: statusColor.opacity(0.1 + (breath * 0.06)), radius: 12, y: 4)
        .offset(y: 3)
    }

    private func activePresence(phase: TimeInterval) -> some View {
        HStack(spacing: 10) {
            LivingIris(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                awake: true,
                motionEnabled: !reduceMotion
            )
            .frame(width: 44, height: 32)

            VStack(alignment: .leading, spacing: 2) {
                Text("JARVIS")
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .tracking(1.7)
                    .foregroundStyle(.white.opacity(0.94))
                Text(statusTitle.uppercased())
                    .font(.system(size: 7.5, weight: .bold, design: .monospaced))
                    .tracking(0.7)
                    .foregroundStyle(statusColor)
                    .contentTransition(.opacity)
            }

            Spacer(minLength: 8)

            NeuralWave(
                color: statusColor,
                energy: visualEnergy,
                phase: phase
            )
            .frame(width: 42, height: 26)

            Image(systemName: statusSymbol)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(statusColor.opacity(0.9))
                .contentTransition(.symbolEffect(.replace))
        }
        .padding(.horizontal, 13)
        .frame(width: min(notchWidth + 116, 286), height: 46)
        .background(surfaceGradient, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .background {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(statusColor.opacity(0.16 + (idleBreath(phase) * 0.07)))
                .blur(radius: 13)
                .scaleEffect(x: 1.04, y: 0.78)
        }
        .overlay {
            RoundedRectangle(cornerRadius: 15, style: .continuous)
                .stroke(
                    LinearGradient(
                        colors: [statusColor.opacity(0.42), .purple.opacity(0.22)],
                        startPoint: .leading,
                        endPoint: .trailing
                    ),
                    lineWidth: 1
                )
        }
        .shadow(color: statusColor.opacity(0.22), radius: 16, y: 5)
        .offset(y: 4)
    }

    private var notchStem: some View {
        UnevenRoundedRectangle(bottomLeadingRadius: 8, bottomTrailingRadius: 8)
            .fill(surfaceGradient)
            .frame(width: notchWidth, height: notchHeight + 8)
            .overlay {
                UnevenRoundedRectangle(bottomLeadingRadius: 8, bottomTrailingRadius: 8)
                    .stroke(
                        LinearGradient(
                            colors: [.clear, statusColor.opacity(0.2)],
                            startPoint: .top,
                            endPoint: .bottom
                        ),
                        lineWidth: 1
                    )
            }
    }

    private var surfaceGradient: LinearGradient {
        LinearGradient(
            colors: [.black, Color(red: 0.015, green: 0.024, blue: 0.042)],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    private var prominent: Bool {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return true
        }
        if activeSwarmRole != nil {
            return true
        }
        switch model.voiceState {
        case .listening, .submitting, .processing, .awaitingApproval, .speaking, .failed:
            return true
        case .idle, .completed:
            return false
        }
    }

    private var statusTitle: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "Protección activada"
        }
        if
            model.voiceState == .idle || model.voiceState == .completed,
            let activeSwarmRole
        {
            return swarmStatusTitle(activeSwarmRole)
        }
        switch model.voiceState {
        case .idle: return wakeWordAwake ? "En escucha ambiental" : "En espera"
        case .listening: return "Te escucho"
        case .submitting: return "Enlazando"
        case .processing: return "Procesando"
        case .awaitingApproval: return "Acción en espera"
        case .speaking: return "Respondiendo"
        case .completed: return "Listo"
        case .failed: return "Revisar sistema"
        }
    }

    private var restingSubtitle: String {
        model.voiceState == .completed
            ? "RESPUESTA COMPLETA"
            : wakeWordAwake ? "ESCUCHA AMBIENTAL" : "EN ESPERA"
    }

    private var statusSymbol: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "exclamationmark.shield.fill"
        }
        if
            model.voiceState == .idle || model.voiceState == .completed,
            activeSwarmRole != nil
        {
            return "circle.hexagongrid.fill"
        }
        switch model.voiceState {
        case .idle: return "sparkles"
        case .listening: return "waveform"
        case .submitting: return "arrow.up"
        case .processing: return "brain.head.profile"
        case .awaitingApproval: return "hand.raised.fill"
        case .speaking: return "speaker.wave.2.fill"
        case .completed: return "checkmark"
        case .failed: return "exclamationmark.triangle.fill"
        }
    }

    private var statusColor: Color {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return .red
        }
        if model.daemonState == .offline || model.securityState == .unavailable {
            return .orange
        }
        if
            model.voiceState == .idle || model.voiceState == .completed,
            activeSwarmRole != nil
        {
            return .purple
        }
        switch model.voiceState {
        case .awaitingApproval: return .orange
        case .failed: return .red
        case .completed: return .green
        case .processing, .submitting: return .purple
        case .idle, .listening, .speaking: return .cyan
        }
    }

    private var visualEnergy: CGFloat {
        if
            model.voiceState == .idle || model.voiceState == .completed,
            activeSwarmRole != nil
        {
            return 0.72
        }
        switch model.voiceState {
        case .listening:
            return max(0.3, CGFloat(model.voiceActivityLevel))
        case .submitting, .processing, .awaitingApproval, .speaking:
            return 0.78
        case .failed:
            return 0.58
        case .idle, .completed:
            return wakeWordAwake ? 0.24 : 0.16
        }
    }

    private var wakeWordAwake: Bool {
        model.wakeWordListeningState == .listening
    }

    private var activeSwarmRole: IPCSwarmAgentRole? {
        model.hudActivity.keys.sorted { $0.rawValue < $1.rawValue }.first
    }

    private func swarmStatusTitle(_ role: IPCSwarmAgentRole) -> String {
        switch role {
        case .router: "Enrutando"
        case .planner: "Planificando"
        case .criticalReasoner: "Razonando"
        case .codeSecurity: "Código y seguridad"
        case .vision: "Analizando visión"
        case .omni: "Coordinando agentes"
        case .synthesizer: "Sintetizando"
        }
    }

    private var refreshInterval: TimeInterval {
        prominent ? 1 / 30 : 1 / 10
    }

    private var stateAnimation: Animation {
        reduceMotion
            ? .easeOut(duration: 0.12)
            : .spring(response: 0.38, dampingFraction: 0.84, blendDuration: 0.08)
    }

    private func idleBreath(_ phase: TimeInterval) -> CGFloat {
        CGFloat((sin(phase * 1.45) + 1) / 2)
    }
}

private struct AmbientWing: View {
    let color: Color
    let energy: CGFloat
    let phase: TimeInterval
    let mirrored: Bool

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<3, id: \.self) { index in
                Capsule()
                    .fill(color.opacity(opacity(index)))
                    .frame(width: CGFloat(3 + (index * 2)), height: index == 2 ? 3 : 2)
                    .offset(y: ripple(index))
            }
            Circle()
                .fill(color.opacity(0.52 + (Double(energy) * 0.35)))
                .frame(width: 3.5, height: 3.5)
                .shadow(color: color.opacity(0.6), radius: 3 + pulse)
        }
        .scaleEffect(x: mirrored ? -1 : 1)
        .frame(width: 26, height: 18)
        .accessibilityHidden(true)
    }

    private var pulse: CGFloat {
        CGFloat((sin(phase * 1.7) + 1) / 2) * (1 + energy)
    }

    private func opacity(_ index: Int) -> Double {
        0.18 + (Double(index) * 0.13) + (Double(energy) * 0.35) + (Double(pulse) * 0.04)
    }

    private func ripple(_ index: Int) -> CGFloat {
        CGFloat(sin((phase * 1.8) + (Double(index) * 0.9))) * (0.4 + (energy * 0.8))
    }
}

private struct LivingIris: View {
    let color: Color
    let energy: CGFloat
    let phase: TimeInterval
    let awake: Bool
    let motionEnabled: Bool

    var body: some View {
        ZStack {
            ZStack {
                Capsule()
                    .stroke(color.opacity(0.18), lineWidth: 5)
                Capsule()
                    .trim(from: 0.06, to: 0.82)
                    .stroke(
                        LinearGradient(
                            colors: [color, .purple.opacity(0.8), color.opacity(0.28)],
                            startPoint: .leading,
                            endPoint: .trailing
                        ),
                        style: StrokeStyle(lineWidth: 1.5, lineCap: .round)
                    )
                    .rotationEffect(.degrees(rotation))

                Circle()
                    .stroke(color.opacity(0.26), lineWidth: 1)
                    .frame(width: 13 + (energy * 4), height: 13 + (energy * 4))
                    .offset(x: gazeX, y: gazeY)

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [.white.opacity(0.96), color, color.opacity(0.16)],
                            center: .center,
                            startRadius: 0,
                            endRadius: 6
                        )
                    )
                    .frame(width: pupilSize, height: pupilSize)
                    .offset(x: gazeX, y: gazeY)
                    .shadow(color: color, radius: 4 + (energy * 3))
            }
            .scaleEffect(y: blinkScale)

            if awake {
                Circle()
                    .fill(.green)
                    .frame(width: 3.5, height: 3.5)
                    .shadow(color: .green, radius: 3)
                    .offset(x: 14, y: -8)
            }
        }
        .scaleEffect(1 + (energy * 0.035))
        .accessibilityHidden(true)
    }

    private var gazeX: CGFloat {
        guard motionEnabled else { return 0 }
        return CGFloat(sin(phase * 0.72)) * (2.2 + (energy * 1.6))
    }

    private var gazeY: CGFloat {
        guard motionEnabled else { return 0 }
        return CGFloat(sin((phase * 0.47) + 1.4)) * (0.7 + energy)
    }

    private var rotation: Double {
        motionEnabled ? phase * (awake ? 22 : 10) : 0
    }

    private var pupilSize: CGFloat {
        let focus = motionEnabled ? CGFloat((sin(phase * 1.65) + 1) / 2) : 0.5
        return 7 + (energy * 3) + (focus * 1.2)
    }

    private var blinkScale: CGFloat {
        guard motionEnabled else { return 1 }
        let duration = awake ? 5.7 : 7.1
        let progress = phase.truncatingRemainder(dividingBy: duration)
        let halfBlink = 0.16
        if progress < halfBlink {
            return max(0.12, 1 - (CGFloat(progress / halfBlink) * 0.88))
        }
        if progress < halfBlink * 2 {
            return 0.12 + (CGFloat((progress - halfBlink) / halfBlink) * 0.88)
        }
        return 1
    }
}

private struct NeuralMotes: View {
    let color: Color
    let energy: CGFloat
    let phase: TimeInterval
    let active: Bool

    var body: some View {
        ZStack {
            ForEach(0..<7, id: \.self) { index in
                Circle()
                    .fill(index.isMultiple(of: 3) ? .white : color)
                    .frame(width: size(index), height: size(index))
                    .shadow(color: color.opacity(0.7), radius: active ? 4 : 2)
                    .offset(x: x(index), y: y(index))
                    .opacity(opacity(index))
            }
        }
        .accessibilityHidden(true)
    }

    private func angle(_ index: Int) -> Double {
        (phase * (active ? 0.9 : 0.38)) + (Double(index) * 0.897)
    }

    private func x(_ index: Int) -> CGFloat {
        CGFloat(cos(angle(index))) * CGFloat(62 + ((index % 3) * 13))
    }

    private func y(_ index: Int) -> CGFloat {
        25 + (CGFloat(sin(angle(index) * 1.17)) * CGFloat(10 + (index % 2) * 5))
    }

    private func size(_ index: Int) -> CGFloat {
        1.4 + (CGFloat(index % 3) * 0.55) + (energy * 0.6)
    }

    private func opacity(_ index: Int) -> Double {
        let shimmer = (sin((phase * 1.8) + Double(index)) + 1) / 2
        return 0.12 + (Double(energy) * 0.32) + (shimmer * (active ? 0.32 : 0.12))
    }
}

private struct NeuralWave: View {
    let color: Color
    let energy: CGFloat
    let phase: TimeInterval

    var body: some View {
        HStack(alignment: .center, spacing: 2) {
            ForEach(0..<7, id: \.self) { index in
                Capsule()
                    .fill(color.opacity(0.28 + (Double(energy) * 0.58)))
                    .frame(width: 2, height: height(index))
            }
        }
        .accessibilityHidden(true)
    }

    private func height(_ index: Int) -> CGFloat {
        let wave = abs(sin((phase * (2.2 + Double(energy))) + (Double(index) * 0.72)))
        return 3 + (CGFloat(wave) * (4 + (energy * 15)))
    }
}
