import AegisAudioCore
import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var forceReduceMotion = false

    @Environment(\.colorSchemeContrast) private var contrast
    private var highContrast: Bool { contrast == .increased }

    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }

    private var notchWidth: CGFloat { presentation.notchWidth }
    private var notchHeight: CGFloat { presentation.notchHeight }
    private var wingWidth: CGFloat { presentation.wingWidth }

    var body: some View {
        TimelineView(
            .animation(
                minimumInterval: reduceMotion ? 1 : refreshInterval,
                paused: reduceMotion || !status.isAnimated
            )
        ) { timeline in
            presence(phase: reduceMotion || !status.isAnimated ? 0 : timeline.date.timeIntervalSinceReferenceDate)
        }
        .frame(width: notchWidth + 2 * wingWidth, height: notchHeight)
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.accessibilityDescription)
    }

    /// The center is the physical camera housing. Draw only in its visible
    /// side strips, with one black silhouette joined to the screen's top edge.
    private func presence(phase: TimeInterval) -> some View {
        HStack(spacing: 0) {
            HStack(spacing: 4) {
                ZStack {
                    LivingIris(
                        color: statusColor, energy: visualEnergy, phase: phase,
                        engaged: wakeWordAwake || prominent,
                        listening: wakeWordAwake || model.voiceState == .listening
                            || model.voiceState == .followingUp,
                        motionEnabled: !reduceMotion
                    )
                    .opacity(showsSwarmGlyph ? 0 : 1)
                    SwarmGlyph(color: statusColor, energy: visualEnergy,
                               phase: phase, activity: model.hudActivity)
                        .opacity(showsSwarmGlyph ? 1 : 0)
                }
                .frame(width: 30, height: max(1, min(20, notchHeight - 8)))
                NeuralWave(color: statusColor, energy: visualEnergy, phase: phase)
                    .frame(width: 16, height: 18)
                    .scaleEffect(x: 0.55)
            }
            .frame(width: wingWidth, height: notchHeight)
            .clipped()

            // Nothing is placed behind the camera or below its lower edge.
            Color.black.frame(width: notchWidth, height: notchHeight)

            VStack(alignment: .leading, spacing: 2) {
                Text("JARVIS")
                    .font(.system(size: 8, weight: .bold, design: .rounded))
                    .tracking(0.8)
                    .foregroundStyle(.white.opacity(0.94))
                Text(displaySubtitle)
                    .font(.system(size: 8.5, weight: .semibold, design: .rounded))
                    .foregroundStyle(statusColor.opacity(highContrast || prominent ? 1 : 0.9))
                    .lineLimit(1).minimumScaleFactor(0.65)
                    .contentTransition(.opacity)
            }
            .padding(.horizontal, 5)
            .frame(width: wingWidth, height: notchHeight, alignment: .leading)
            .clipped()
        }
        .background(.black)
        .clipShape(UnevenRoundedRectangle(
            bottomLeadingRadius: min(10, notchHeight / 3),
            bottomTrailingRadius: min(10, notchHeight / 3), style: .continuous
        ))
        .animation(stateTransitionAnimation, value: status)
    }

    private var status: AssistantPresentation { model.presentationStatus }
    private var prominent: Bool { status.isProminent }
    private var displaySubtitle: String { status.compactTitle.uppercased() }
    private var statusColor: Color { status.tone.color }
    private var showsSwarmGlyph: Bool {
        (status.kind == .working || status.kind == .processing) && activeSwarmRole != nil
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
        SwarmRoleVisuals.orderedRoles.first { model.hudActivity[$0, default: 0] > 0 }
    }

    private var refreshInterval: TimeInterval {
        prominent ? 1 / 30 : 1 / 10
    }

    private var stateTransitionAnimation: Animation? {
        reduceMotion ? nil : .easeInOut(duration: 0.22)
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
