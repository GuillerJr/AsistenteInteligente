import SwiftUI

enum JarvisNotchDotMood { case resting, listening, thinking, speaking, attention }

/// Small procedural artwork, with its animation clock isolated from the controls.
struct JarvisNotchDot: View {
    let color: Color
    let mood: JarvisNotchDotMood
    let energy: Double
    var engaged = false
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }

    var body: some View {
        TimelineView(.animation(minimumInterval: mood == .resting ? 1 / 12 : 1 / 30,
                                paused: reduceMotion)) { timeline in
            GeometryReader { geometry in
                let size = min(geometry.size.width, geometry.size.height)
                let phase = reduceMotion ? 0 : timeline.date.timeIntervalSinceReferenceDate
                let level = reduceMotion ? 0 : min(max(energy, 0), 1)
                let breath = reduceMotion ? 0 : sin(phase * (mood == .speaking ? 5 : 1.8))
                ZStack {
                    Circle().fill(color.opacity(0.12))
                        .scaleEffect(0.82 + breath * 0.04)
                    NotchDotShape(phase: phase, energy: mood == .resting ? 0.12 : level)
                        .fill(RadialGradient(colors: [.white.opacity(0.98), color, color.opacity(0.68)],
                                             center: .init(x: 0.34, y: 0.28), startRadius: 0,
                                             endRadius: size * 0.62))
                        .shadow(color: color.opacity(0.45), radius: size * 0.09)
                        .scaleEffect((engaged ? 1.08 : 1) + breath * 0.025)
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: energy)

                    if mood == .thinking {
                        Circle().trim(from: 0.06, to: 0.48)
                            .stroke(color.opacity(0.8), style: StrokeStyle(lineWidth: max(1, size * 0.025),
                                                                         lineCap: .round))
                            .padding(size * 0.035)
                            .rotationEffect(.degrees(reduceMotion ? 0 : phase * 85))
                    }
                    if mood == .listening || mood == .speaking {
                        HStack(spacing: size * 0.045) {
                            ForEach(0..<4, id: \.self) { index in
                                let wave = reduceMotion ? 0.5 : abs(sin(phase * 5 + Double(index)))
                                Capsule().fill(.white.opacity(0.85))
                                    .frame(width: max(1, size * 0.045),
                                           height: size * (0.07 + (0.08 + level * 0.17) * wave))
                            }
                        }
                    }
                    if mood == .attention {
                        Image(systemName: "exclamationmark")
                            .font(.system(size: size * 0.28, weight: .bold, design: .rounded))
                            .foregroundStyle(.black.opacity(0.65))
                    }
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
            }
        }
        .accessibilityHidden(true)
    }
}

private struct NotchDotShape: Shape {
    var phase: Double
    var energy: Double
    var animatableData: AnimatablePair<Double, Double> {
        get { .init(phase, energy) }
        set { phase = newValue.first; energy = newValue.second }
    }

    func path(in rect: CGRect) -> Path {
        let radius = min(rect.width, rect.height) * 0.31
        let amplitude = 0.035 + min(max(energy, 0), 1) * 0.075
        let points = (0..<80).map { index in
            let angle = Double(index) * .pi * 2 / 80
            let wobble = sin(angle * 3 + phase * 1.6) + sin(angle * 5 - phase * 1.1) * 0.45
            let distance = radius * (1 + wobble * amplitude)
            return CGPoint(x: rect.midX + cos(angle) * distance, y: rect.midY + sin(angle) * distance)
        }
        return Path { path in path.addLines(points); path.closeSubpath() }
    }
}
