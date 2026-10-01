import AegisAudioCore
import AppKit
import SwiftUI

enum JarvisMascotMood { case resting, listening, thinking, speaking, attention }

extension JarvisMascotMood {
    init(kind: AssistantPresentation.Kind) {
        self = switch kind {
        case .listening, .followingUp: .listening
        case .processing, .submitting, .working, .connecting, .securityChecking, .brainChecking: .thinking
        case .speaking: .speaking
        case .approval, .browserSelection, .failure, .securityBlocked, .offline,
             .securityUnavailable, .brainMissing, .brainUnavailable: .attention
        default: .resting
        }
    }
}

/// A bundled plush texture with an independently animated face. The clock is
/// confined to this artwork, rather than redrawing the surrounding application.
struct JarvisNotchMascot: View {
    let mood: JarvisMascotMood
    let energy: Double
    var engaged = false
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    private static let bodyImage: Image = {
        guard let url = Bundle.module.url(forResource: "JarvisMascotBody", withExtension: "png"),
              let image = NSImage(contentsOf: url) else {
            preconditionFailure("The bundled Jarvis mascot texture is missing")
        }
        return Image(nsImage: image)
    }()

    var body: some View {
        let reduced = systemReduceMotion || forceReduceMotion
        let image = Self.bodyImage
        TimelineView(.animation(minimumInterval: mood == .resting ? 1 / 24 : 1 / 30,
                                paused: reduced)) { timeline in
            Canvas { context, size in
                let phase = reduced ? 0 : timeline.date.timeIntervalSinceReferenceDate
                let level = reduced ? 0 : min(max(energy, 0), 1)
                let rhythm = reduced ? 0 : sin(phase * (mood == .speaking ? 7 : 1.8))
                let side = min(size.width, size.height) * 0.94
                let breath = 1 + rhythm * (mood == .speaking ? 0.022 : 0.012)
                let tilt = reduced ? 0 : sin(phase * 1.3) * (mood == .thinking ? 0.055 : 0.012)
                context.translateBy(x: size.width / 2, y: size.height / 2)
                context.rotate(by: .radians(tilt))
                context.scaleBy(x: side * breath, y: side / breath)
                context.translateBy(x: -0.5, y: -0.5)
                context.draw(context.resolve(image), in: CGRect(x: 0, y: 0, width: 1, height: 1))
                drawFace(in: &context, phase: phase, level: level, reduced: reduced)
            }
        }
        .accessibilityHidden(true)
    }

    private func drawFace(in context: inout GraphicsContext, phase: Double, level: Double, reduced: Bool) {
        let cycle = phase.truncatingRemainder(dividingBy: 5.8)
        let blink = reduced ? 1 : max(0.12, 1 - max(0, 1 - abs(cycle - 5.25) / 0.13))
        let alert = mood == .listening || mood == .attention
        let eyeHeight = (alert ? 0.078 : 0.066) * blink
        let glanceX = mood == .thinking && !reduced ? sin(phase * 1.2) * 0.012 : 0
        let glanceY = mood == .thinking ? -0.012 : (engaged ? -0.005 : 0)
        let faceY = 0.425 + glanceY
        let ink = Color(red: 0.025, green: 0.04, blue: 0.045)
        for x in [0.36, 0.63] {
            let center = CGPoint(x: x + glanceX, y: faceY)
            let eye = Path(ellipseIn: CGRect(x: center.x - 0.066, y: center.y - eyeHeight,
                                            width: 0.132, height: eyeHeight * 2))
            context.fill(eye, with: .radialGradient(
                Gradient(colors: [Color(white: 0.16), ink, .black]),
                center: CGPoint(x: center.x - 0.025, y: center.y - eyeHeight * 0.45),
                startRadius: 0, endRadius: 0.12))
            if blink > 0.3 {
                context.fill(Path(ellipseIn: CGRect(x: center.x - 0.032, y: center.y - eyeHeight * 0.6,
                                                   width: 0.039, height: 0.043 * blink)),
                             with: .color(.white.opacity(0.92)))
            }
        }

        // The monocle stays rigid while the eye beneath it blinks or glances.
        let lens = CGRect(x: 0.525, y: 0.32, width: 0.21, height: 0.21)
        context.fill(Path(ellipseIn: lens), with: .color(.cyan.opacity(0.035)))
        context.stroke(Path(ellipseIn: lens), with: .color(ink), lineWidth: 0.027)
        context.stroke(Path(ellipseIn: lens.insetBy(dx: 0.018, dy: 0.018)),
                       with: .color(Color(white: 0.28).opacity(0.7)), lineWidth: 0.009)
        var tail = Path()
        tail.move(to: CGPoint(x: 0.718, y: 0.482))
        tail.addCurve(to: CGPoint(x: 0.845, y: 0.637),
                      control1: CGPoint(x: 0.725, y: 0.598), control2: CGPoint(x: 0.758, y: 0.645))
        context.stroke(tail, with: .color(ink), style: StrokeStyle(lineWidth: 0.028, lineCap: .round))

        if mood == .speaking {
            let opening = reduced ? 0.018 : 0.015 + abs(sin(phase * 8)) * (0.02 + level * 0.018)
            context.fill(Path(ellipseIn: CGRect(x: 0.465, y: 0.575, width: 0.06, height: opening)),
                         with: .color(ink.opacity(0.85)))
        } else if mood == .attention {
            var brow = Path()
            brow.move(to: CGPoint(x: 0.32, y: 0.315))
            brow.addQuadCurve(to: CGPoint(x: 0.40, y: 0.30), control: CGPoint(x: 0.35, y: 0.285))
            context.stroke(brow, with: .color(ink.opacity(0.75)),
                           style: StrokeStyle(lineWidth: 0.014, lineCap: .round))
        } else if mood == .listening && level > 0 {
            context.fill(Path(ellipseIn: CGRect(x: 0.469, y: 0.582, width: 0.052,
                                               height: 0.008 + level * 0.016)),
                         with: .color(ink.opacity(0.65)))
        }
    }
}
