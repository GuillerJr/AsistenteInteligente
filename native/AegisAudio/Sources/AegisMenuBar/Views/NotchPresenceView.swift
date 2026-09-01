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
            .frame(width: min(notchWidth + 112, 272), height: 58)
            .offset(y: 1)

            neuralBridge(phase: phase)

            corePresence(phase: phase)
        }
        .animation(stateTransitionAnimation, value: model.voiceState)
        .animation(stateTransitionAnimation, value: activeSwarmRole)
        .animation(stateTransitionAnimation, value: model.securityState)
        .animation(stateTransitionAnimation, value: model.providerState)
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

    private func corePresence(phase: TimeInterval) -> some View {
        let breath = idleBreath(phase)
        let shape = UnevenRoundedRectangle(
            topLeadingRadius: 4,
            bottomLeadingRadius: 15,
            bottomTrailingRadius: 15,
            topTrailingRadius: 4,
            style: .continuous
        )
        return HStack(spacing: 9) {
            LivingIris(
                color: statusColor,
                energy: visualEnergy,
                phase: phase,
                engaged: wakeWordAwake || prominent,
                listening: wakeWordAwake
                    || model.voiceState == .listening
                    || model.voiceState == .followingUp,
                motionEnabled: !reduceMotion
            )
            .frame(width: 40, height: 28)

            VStack(alignment: .leading, spacing: 2) {
                Text("JARVIS")
                    .font(.system(size: 9.5, weight: .bold, design: .rounded))
                    .tracking(1.65)
                    .foregroundStyle(.white.opacity(0.94))
                Text(displaySubtitle)
                    .font(.system(size: 7.4, weight: .bold, design: .monospaced))
                    .tracking(0.58)
                    .foregroundStyle(statusColor.opacity(prominent ? 0.96 : 0.78))
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                    .contentTransition(.opacity)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            ZStack {
                NeuralWave(
                    color: statusColor,
                    energy: max(0.12, prominent ? visualEnergy : visualEnergy * 0.55),
                    phase: phase
                )
                .frame(width: 34, height: 21)
                .opacity(showsSwarmGlyph ? 0 : 1)
                .scaleEffect(reduceMotion || !showsSwarmGlyph ? 1 : 0.86)

                SwarmGlyph(
                    color: statusColor,
                    energy: visualEnergy,
                    phase: phase,
                    activity: model.hudActivity
                )
                .frame(width: 26, height: 26)
                .opacity(showsSwarmGlyph ? 1 : 0)
                .scaleEffect(reduceMotion || showsSwarmGlyph ? 1 : 0.86)
            }
            .frame(width: 36, height: 28)
        }
        .padding(.horizontal, 13)
        .frame(width: min(notchWidth + 100, 254), height: 42)
        .background(surfaceGradient, in: shape)
        .background {
            shape
                .fill(statusColor.opacity(0.09 + (breath * (prominent ? 0.07 : 0.04))))
                .blur(radius: 13)
                .scaleEffect(x: 1.035, y: 0.76)
        }
        .overlay {
            shape.stroke(
                LinearGradient(
                    colors: [
                        statusColor.opacity(prominent ? 0.5 : 0.32),
                        .white.opacity(0.07),
                        statusColor.opacity(prominent ? 0.24 : 0.14),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                lineWidth: 1
            )
        }
        .shadow(
            color: statusColor.opacity(prominent ? 0.2 : 0.11),
            radius: prominent ? 16 : 13,
            y: 5
        )
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
        case .listening, .followingUp, .submitting, .processing,
             .awaitingAuthorization, .speaking, .failed:
            return true
        case .idle, .completed:
            return false
        }
    }

    private var statusTitle: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "Protección activada"
        }
        switch model.providerState {
        case .missing: return "Credencial NVIDIA ausente"
        case .unavailable: return "NVIDIA no verificable"
        case .unknown, .checking, .configured: break
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
        case .followingUp: return "Puedes continuar"
        case .submitting: return "Enlazando"
        case .processing: return "Procesando"
        case .awaitingAuthorization: return "Usa Touch ID o di ‘Aprobado’"
        case .speaking: return "Respondiendo"
        case .completed: return "Listo"
        case .failed: return "Revisar sistema"
        }
    }

    private var restingSubtitle: String {
        switch model.providerState {
        case .missing: "NVIDIA SIN CREDENCIAL"
        case .unavailable: "NVIDIA NO VERIFICABLE"
        case .unknown, .checking, .configured:
            model.voiceState == .completed
                ? "RESPUESTA COMPLETA"
                : wakeWordAwake ? "ESCUCHA AMBIENTAL" : "EN ESPERA"
        }
    }

    private var displaySubtitle: String {
        prominent ? statusTitle.uppercased() : restingSubtitle
    }

    private var showsSwarmGlyph: Bool {
        activeSwarmRole != nil
    }

    private var statusColor: Color {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return .red
        }
        if model.daemonState == .offline || model.securityState == .unavailable {
            return .orange
        }
        if model.providerState == .missing || model.providerState == .unavailable {
            return .orange
        }
        if
            model.voiceState == .idle || model.voiceState == .completed,
            activeSwarmRole != nil
        {
            return .purple
        }
        switch model.voiceState {
        case .awaitingAuthorization: return .orange
        case .failed: return .red
        case .completed: return .green
        case .processing, .submitting: return .purple
        case .idle, .listening, .followingUp, .speaking: return .cyan
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
        case .followingUp:
            return max(0.18, CGFloat(model.voiceActivityLevel) * 0.55)
        case .submitting, .processing, .awaitingAuthorization, .speaking:
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

    private var stateTransitionAnimation: Animation {
        reduceMotion ? .easeOut(duration: 0.1) : .easeInOut(duration: 0.22)
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
    let engaged: Bool
    let listening: Bool
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

            if listening {
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
        motionEnabled ? phase * (engaged ? 22 : 10) : 0
    }

    private var pupilSize: CGFloat {
        let focus = motionEnabled ? CGFloat((sin(phase * 1.65) + 1) / 2) : 0.5
        return 7 + (energy * 3) + (focus * 1.2)
    }

    private var blinkScale: CGFloat {
        guard motionEnabled else { return 1 }
        let duration = engaged ? 5.7 : 7.1
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
