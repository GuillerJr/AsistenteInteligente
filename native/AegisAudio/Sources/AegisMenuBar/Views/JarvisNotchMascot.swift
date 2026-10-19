import AegisAudioCore
import AppKit
import SwiftUI

/// An original emerald trickster dot, with a face rendered independently of its skin.
/// The animation clock stays confined to the artwork.
struct JarvisNotchMascot: View {
    let behavior: MascotBehaviorState
    let energy: Double
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    private static let bodyImage: Image = {
        guard let url = Bundle.module.url(forResource: "JarvisTricksterDot", withExtension: "png"),
              let image = NSImage(contentsOf: url) else {
            preconditionFailure("The bundled Jarvis mascot texture is missing")
        }
        return Image(nsImage: image)
    }()

    var body: some View {
        let reduced = systemReduceMotion || forceReduceMotion
        let image = Self.bodyImage
        // Reduced motion still observes timed states at 1 Hz, without body movement.
        TimelineView(.animation(minimumInterval: reduced ? 1 : 1 / 24)) { timeline in
            Canvas { context, size in
                let phase = reduced ? 0 : timeline.date.timeIntervalSinceReferenceDate
                let pose = behavior.pose(at: timeline.date.timeIntervalSinceReferenceDate, reducedMotion: reduced)
                let mood = pose.phase
                let level = reduced ? 0 : min(max(energy, 0), 1)
                let rhythm = reduced ? 0 : sin(phase * (mood == .speaking ? 7 : 1.8))
                let side = min(size.width, size.height) * 0.94
                let breath = 1 + rhythm * (mood == .speaking ? 0.022 : 0.012)
                let tilt = pose.tilt
                context.translateBy(x: size.width / 2, y: size.height / 2)
                context.translateBy(x: 0, y: pose.nod + (reduced || mood == .sleeping || mood == .paused ? 0 : sin(phase * 1.1) * side * 0.006))
                context.rotate(by: .radians(tilt))
                context.scaleBy(x: side * breath, y: side / breath)
                context.translateBy(x: -0.5, y: -0.5)
                context.draw(context.resolve(image), in: CGRect(x: 0, y: 0, width: 1, height: 1))
                drawFace(in: &context, pose: pose, phase: phase, level: level, reduced: reduced)
            }
        }
        .accessibilityHidden(true)
    }

    private func drawFace(in context: inout GraphicsContext, pose: MascotBehaviorState.Pose, phase: Double, level: Double, reduced: Bool) {
        let mood = pose.phase
        let cycle = phase.truncatingRemainder(dividingBy: 5.8)
        let blink = pose.eyeOpenness * (reduced ? 1 : max(0.12, 1 - max(0, 1 - abs(cycle - 5.25) / 0.13)))
        let alert = mood == .listening || mood == .attention
        let light = mood == .attention ? Color(red: 1, green: 0.76, blue: 0.32)
                                      : Color(red: 0.87, green: 0.94, blue: 0.62)
        let glanceX = pose.gazeX
        let faceY = 0.515 + pose.gazeY + (mood == .thinking ? -0.008 : 0)
        for (index, x) in [0.39, 0.61].enumerated() {
            let width = alert ? 0.122 : 0.108
            let height = (alert ? 0.142 : 0.124) * blink * (mood == .thinking && index == 0 ? 0.78 : 1)
            let eyeRect = CGRect(x: x + glanceX - width / 2, y: faceY - height / 2,
                                 width: width, height: height)
            let eye = Path(roundedRect: eyeRect, cornerRadius: min(width, height) * 0.46)
            context.stroke(eye, with: .color(light.opacity(alert ? 0.75 : 0.38)), lineWidth: 0.006)
            context.fill(eye, with: .linearGradient(Gradient(colors: [Color(red: 0.045, green: 0.09, blue: 0.06), .black]),
                         startPoint: CGPoint(x: x, y: eyeRect.minY),
                         endPoint: CGPoint(x: x, y: eyeRect.maxY)))
            // Glossy dot eyes: highlights follow eyelids, so sleep never leaves
            // floating glints outside the compressed eye shape.
            var highlights = context
            highlights.clip(to: eye)
            highlights.fill(Path(ellipseIn: CGRect(x: eyeRect.minX + width * 0.22,
                                                   y: eyeRect.minY + height * 0.19,
                                                   width: width * 0.29, height: height * 0.25)),
                            with: .color(.white.opacity(0.97)))
            highlights.fill(Path(ellipseIn: CGRect(x: eyeRect.minX + width * 0.65,
                                                   y: eyeRect.minY + height * 0.66,
                                                   width: width * 0.12, height: height * 0.11)),
                            with: .color(light.opacity(0.5)))
        }

        drawVisorMouth(in: &context, mood: mood, light: light, phase: phase, level: level, reduced: reduced)
        if mood == .attention {
            var brow = Path()
            brow.move(to: CGPoint(x: 0.35, y: 0.43))
            brow.addQuadCurve(to: CGPoint(x: 0.42, y: 0.425), control: CGPoint(x: 0.38, y: 0.413))
            context.stroke(brow, with: .color(light.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 0.009, lineCap: .round))
        }
        // A small light inside the brass clasp is Jarvis's characteristic
        // intelligence beacon; all geometry remains inside the bundled body.
        let pulse = reduced || [.resting, .sleeping, .paused].contains(mood) ? 0.65 : 0.65 + sin(phase * 3) * 0.25
        var beacon = Path()
        beacon.addLines([CGPoint(x: 0.491, y: 0.777), CGPoint(x: 0.5, y: 0.789),
                         CGPoint(x: 0.491, y: 0.801), CGPoint(x: 0.482, y: 0.789)])
        beacon.closeSubpath()
        context.fill(beacon, with: .color(light.opacity(pulse)))
    }

    private func drawVisorMouth(in context: inout GraphicsContext, mood: MascotBehaviorState.Phase, light: Color,
                                phase: Double, level: Double, reduced: Bool) {
        switch mood {
        case .speaking:
            for index in 0..<3 {
                let wave = reduced ? 0.5 : abs(sin(phase * 7 + Double(index) * 0.9))
                let height = 0.012 + wave * (0.018 + level * 0.012)
                context.fill(Path(roundedRect: CGRect(x: 0.469 + Double(index) * 0.025,
                                                       y: 0.588 - height / 2, width: 0.012, height: height),
                                  cornerRadius: 0.006), with: .color(light.opacity(0.9)))
            }
        case .thinking:
            for index in 0..<3 {
                let brightness = reduced ? 0.65 : 0.35 + 0.65 * (0.5 + 0.5 * sin(phase * 4 - Double(index)))
                context.fill(Path(ellipseIn: CGRect(x: 0.472 + Double(index) * 0.023,
                                                   y: 0.581, width: 0.012, height: 0.012)),
                             with: .color(light.opacity(brightness)))
            }
        case .attention, .sleeping, .paused:
            var mouth = Path()
            mouth.move(to: CGPoint(x: 0.474, y: 0.588))
            mouth.addLine(to: CGPoint(x: 0.526, y: 0.588))
            context.stroke(mouth, with: .color(light.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 0.011, lineCap: .round))
        case .resting, .listening, .curious, .greeting, .acknowledging, .celebrating:
            var smile = Path()
            smile.move(to: CGPoint(x: 0.455, y: 0.578))
            smile.addQuadCurve(to: CGPoint(x: 0.55, y: 0.571),
                               control: CGPoint(x: 0.502, y: 0.606 + (reduced ? 0 : level * 0.008)))
            context.stroke(smile, with: .color(light.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 0.011, lineCap: .round))
        }
    }
}
