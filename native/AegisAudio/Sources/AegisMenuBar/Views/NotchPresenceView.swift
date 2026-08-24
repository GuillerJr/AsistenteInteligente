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
            .frame(width: wingWidth, alignment: .trailing)

            Color.clear
                .frame(width: notchWidth)

            AmbientWing(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                mirrored: true
            )
            .frame(width: wingWidth, alignment: .leading)
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
                        .opacity
                            .combined(with: .scale(scale: 0.9, anchor: .top))
                            .combined(with: .offset(y: -4))
                    )
            } else {
                restingPresence(phase: phase)
                    .transition(
                        .opacity
                            .combined(with: .scale(scale: 0.94, anchor: .top))
                            .combined(with: .offset(y: -3))
                    )
            }
        }
        .animation(stateAnimation, value: prominent)
        .animation(.easeOut(duration: 0.2), value: model.voiceState)
        .animation(.easeOut(duration: 0.24), value: activeSwarmRole)
        .animation(.easeOut(duration: 0.24), value: model.securityState)
    }

    private func neuralBridge(phase: TimeInterval) -> some View {
        let breath = idleBreath(phase)
        let scan = CGFloat(sin(phase * (prominent ? 2.4 : 1.15)))
        let bridgeWidth = min(max(notchWidth * 0.52, 84), 124)
        return ZStack {
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [.clear, statusColor.opacity(0.42 + (breath * 0.2)), .clear],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(width: bridgeWidth + (breath * 12), height: 1.5)
                .shadow(color: statusColor.opacity(0.42), radius: 5)

            HStack(spacing: 0) {
                Circle().frame(width: 3, height: 3)
                Spacer(minLength: 0)
                Circle().frame(width: 3, height: 3)
            }
            .foregroundStyle(statusColor.opacity(0.34 + (breath * 0.16)))
            .frame(width: bridgeWidth, height: 3)

            Circle()
                .fill(statusColor)
                .frame(width: 3, height: 3)
                .shadow(color: statusColor, radius: 4 + (breath * 2))
                .offset(x: scan * ((bridgeWidth / 2) - 5))

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
        let shape = UnevenRoundedRectangle(
            topLeadingRadius: 5,
            bottomLeadingRadius: 16,
            bottomTrailingRadius: 16,
            topTrailingRadius: 5,
            style: .continuous
        )
        return HStack(spacing: 10) {
            LivingIris(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                awake: wakeWordAwake,
                motionEnabled: !reduceMotion
            )
            .frame(width: 42, height: 27)

            VStack(alignment: .leading, spacing: 1.5) {
                Text("JARVIS")
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .tracking(1.7)
                    .foregroundStyle(.white.opacity(0.94))
                Text(restingSubtitle)
                    .font(.system(size: 7.2, weight: .semibold, design: .monospaced))
                    .tracking(0.62)
                    .foregroundStyle(statusColor.opacity(0.78))
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            NeuralWave(
                color: statusColor,
                energy: max(0.12, visualEnergy * 0.55),
                phase: phase
            )
            .frame(width: 34, height: 19)
        }
        .padding(.horizontal, 13)
        .frame(width: min(notchWidth + 68, 270), height: 37)
        .background(surfaceGradient, in: shape)
        .background {
            shape
                .fill(statusColor.opacity(0.08 + (breath * 0.05)))
                .blur(radius: 12)
                .scaleEffect(x: 1.04 + (breath * 0.02), y: 0.74)
        }
        .overlay {
            shape.stroke(
                LinearGradient(
                    colors: [
                        statusColor.opacity(0.34 + (breath * 0.08)),
                        .white.opacity(0.06),
                        statusColor.opacity(0.16),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                lineWidth: 1
            )
        }
        .shadow(color: statusColor.opacity(0.12 + (breath * 0.07)), radius: 14, y: 5)
        .offset(y: -1)
    }

    private func activePresence(phase: TimeInterval) -> some View {
        let breath = idleBreath(phase)
        let shape = UnevenRoundedRectangle(
            topLeadingRadius: 7,
            bottomLeadingRadius: 18,
            bottomTrailingRadius: 18,
            topTrailingRadius: 7,
            style: .continuous
        )
        return HStack(spacing: 11) {
            LivingIris(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                awake: true,
                motionEnabled: !reduceMotion
            )
            .frame(width: 46, height: 32)

            VStack(alignment: .leading, spacing: 2) {
                Text("JARVIS")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .tracking(1.8)
                    .foregroundStyle(.white.opacity(0.94))
                Text(statusTitle.uppercased())
                    .font(.system(size: 8, weight: .bold, design: .monospaced))
                    .tracking(0.72)
                    .foregroundStyle(statusColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.78)
                    .contentTransition(.opacity)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            SwarmGlyph(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                activity: model.hudActivity
            )
            .frame(width: 30, height: 30)

            NeuralWave(
                color: statusColor,
                energy: visualEnergy,
                phase: phase
            )
            .frame(width: 36, height: 25)
        }
        .padding(.horizontal, 14)
        .frame(width: min(notchWidth + 126, 304), height: 48)
        .background(surfaceGradient, in: shape)
        .background {
            shape
                .fill(statusColor.opacity(0.14 + (breath * 0.08)))
                .blur(radius: 15)
                .scaleEffect(x: 1.04, y: 0.78)
        }
        .overlay {
            shape.stroke(
                LinearGradient(
                    colors: [
                        statusColor.opacity(0.58),
                        .white.opacity(0.08),
                        .purple.opacity(0.24),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                lineWidth: 1
            )
        }
        .shadow(color: statusColor.opacity(0.24), radius: 18, y: 6)
        .offset(y: -1)
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
            colors: [
                .black,
                Color(red: 0.014, green: 0.026, blue: 0.046),
                Color(red: 0.006, green: 0.012, blue: 0.022),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
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
        ZStack {
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [.clear, color.opacity(0.18 + (Double(energy) * 0.34))],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(width: 30, height: 1)

            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(index == 2 ? color : color.opacity(opacity(index)))
                    .frame(width: index == 2 ? 3 : 2, height: index == 2 ? 3 : 2)
                    .shadow(color: color.opacity(0.45), radius: index == 2 ? 3 : 1)
                    .offset(x: CGFloat(-10 + (index * 10)), y: ripple(index))
            }

            Circle()
                .fill(.white.opacity(0.7))
                .frame(width: 1.5, height: 1.5)
                .shadow(color: color, radius: 2.5 + pulse)
                .offset(x: scanPosition)
        }
        .scaleEffect(x: mirrored ? -1 : 1)
        .frame(width: 30, height: 18)
        .accessibilityHidden(true)
    }

    private var pulse: CGFloat {
        CGFloat((sin(phase * 1.7) + 1) / 2) * (1 + energy)
    }

    private func opacity(_ index: Int) -> Double {
        0.16 + (Double(index) * 0.12) + (Double(energy) * 0.3) + (Double(pulse) * 0.04)
    }

    private func ripple(_ index: Int) -> CGFloat {
        CGFloat(sin((phase * 1.6) + (Double(index) * 0.9))) * (0.3 + (energy * 0.65))
    }

    private var scanPosition: CGFloat {
        CGFloat(sin(phase * (1.2 + Double(energy)))) * 13
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

                Capsule()
                    .trim(from: 0.54, to: 0.96)
                    .stroke(
                        color.opacity(0.42 + (Double(energy) * 0.22)),
                        style: StrokeStyle(lineWidth: 1, lineCap: .round)
                    )
                    .rotationEffect(.degrees(-rotation * 0.62))

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

                Circle()
                    .fill(.white.opacity(0.9))
                    .frame(width: 1.6, height: 1.6)
                    .offset(x: gazeX - 1.5, y: gazeY - 1.5)
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

private struct SwarmGlyph: View {
    private static let roles: [IPCSwarmAgentRole] = [
        .router,
        .planner,
        .criticalReasoner,
        .codeSecurity,
        .vision,
        .omni,
        .synthesizer,
    ]

    let color: Color
    let energy: CGFloat
    let phase: TimeInterval
    let activity: [IPCSwarmAgentRole: Int]

    var body: some View {
        ZStack {
            Circle()
                .stroke(color.opacity(0.12 + (Double(energy) * 0.12)), lineWidth: 1)
                .frame(width: 22, height: 22)

            ForEach(Self.roles.indices, id: \.self) { index in
                let active = activity[Self.roles[index]] != nil
                Circle()
                    .fill(active ? color : color.opacity(0.2))
                    .frame(width: active ? 3.8 : 2.2, height: active ? 3.8 : 2.2)
                    .shadow(color: active ? color : .clear, radius: active ? 4 : 0)
                    .offset(nodeOffset(index))
                    .opacity(active ? activeOpacity(index) : 0.45)
            }

            Circle()
                .fill(
                    RadialGradient(
                        colors: [.white.opacity(0.9), color, color.opacity(0.1)],
                        center: .center,
                        startRadius: 0,
                        endRadius: 4
                    )
                )
                .frame(width: 6 + (energy * 2), height: 6 + (energy * 2))
                .shadow(color: color, radius: 4 + (energy * 2))
        }
        .rotationEffect(.degrees(activity.isEmpty ? 0 : phase * 5))
        .accessibilityHidden(true)
    }

    private func nodeOffset(_ index: Int) -> CGSize {
        let angle = (-Double.pi / 2) + (Double(index) * ((Double.pi * 2) / 7))
        return CGSize(width: cos(angle) * 11, height: sin(angle) * 11)
    }

    private func activeOpacity(_ index: Int) -> Double {
        0.68 + (((sin((phase * 2.2) + Double(index)) + 1) / 2) * 0.32)
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
