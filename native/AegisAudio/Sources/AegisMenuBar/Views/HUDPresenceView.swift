import SwiftUI

/// A restrained, procedural light sculpture; no particles, timers or sensors.
struct HUDPresenceView: View {
    let accent: Color
    let symbol: String
    let animated: Bool
    let voiceLevel: Float
    let listening: Bool
    let highContrast: Bool

    private var level: CGFloat {
        guard voiceLevel.isFinite, listening else { return 0 }
        return CGFloat(min(max(voiceLevel, 0), 1))
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20, paused: !animated)) { timeline in
            let phase = animated ? timeline.date.timeIntervalSinceReferenceDate * 0.22 : 0
            GeometryReader { geometry in
                let diameter = min(geometry.size.height, geometry.size.width) * 0.82
                ZStack {
                    Circle()
                        .fill(RadialGradient(colors: [accent.opacity(0.14), .clear],
                                             center: .center, startRadius: 4, endRadius: diameter * 0.65))
                        .frame(width: diameter * 1.5, height: diameter * 1.5)
                    Circle()
                        .fill(RadialGradient(colors: [accent.opacity(0.1), .clear],
                                             center: .topLeading, startRadius: 0, endRadius: diameter))
                        .frame(width: diameter, height: diameter)
                        .overlay {
                            Circle().strokeBorder(accent.opacity(highContrast ? 0.6 : 0.16), lineWidth: 0.7)
                        }
                    Canvas { context, size in
                        let radius = min(size.width, size.height) * 0.5
                        for strand in 0..<28 {
                            let latitude = Double(strand) / 27 * .pi
                            let ringRadius = sin(latitude)
                            let ringY = cos(latitude)
                            var path = Path()
                            for step in 0...96 {
                                let angle = Double(step) / 96 * .pi * 2
                                let ripple = 1 + 0.045 * sin(angle * 3 + phase + ringY * 2)
                                let x = ringRadius * cos(angle) * ripple
                                let z = ringRadius * sin(angle) * ripple
                                let y = ringY * cos(0.48) - z * sin(0.48)
                                let projectedX = x * cos(0.35) - y * sin(0.35)
                                let projectedY = x * sin(0.35) + y * cos(0.35)
                                let point = CGPoint(x: size.width / 2 + projectedX * radius,
                                                    y: size.height / 2 + projectedY * radius)
                                if step == 0 { path.move(to: point) } else { path.addLine(to: point) }
                            }
                            context.stroke(path, with: .linearGradient(
                                Gradient(colors: [accent.opacity(highContrast ? 0.65 : 0.18),
                                                  accent.opacity(0.85), .white.opacity(0.7),
                                                  accent.opacity(0.15)]),
                                startPoint: .zero, endPoint: CGPoint(x: size.width, y: size.height)
                            ), lineWidth: highContrast ? 0.9 : 0.65)
                        }
                    }
                    .frame(width: diameter, height: diameter)
                    .scaleEffect(1 + level * 0.045)

                    Image(systemName: symbol)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(accent)
                        .frame(width: 30, height: 30)
                        .background(Color(red: 0.1, green: 0.12, blue: 0.145), in: Circle())
                        .overlay(Circle().strokeBorder(accent.opacity(0.25), lineWidth: 0.8))
                        .offset(x: diameter * 0.39, y: diameter * 0.31)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
