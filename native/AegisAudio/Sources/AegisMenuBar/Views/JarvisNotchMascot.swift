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

/// Jarvis's original velvet seed guardian with an independently animated visor.
/// The clock is
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
                context.translateBy(x: 0, y: reduced ? 0 : sin(phase * 1.1) * side * 0.006)
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
        let light = mood == .attention ? Color(red: 1, green: 0.74, blue: 0.32)
                                      : Color(red: 0.60, green: 0.97, blue: 1)
        let glanceX = mood == .thinking && !reduced ? sin(phase * 1.2) * 0.016 : 0
        let faceY = 0.426 + (mood == .thinking ? -0.008 : (engaged ? -0.004 : 0))
        for (index, x) in [0.385, 0.615].enumerated() {
            let width = alert ? 0.084 : 0.074
            let height = (alert ? 0.112 : 0.096) * blink * (mood == .thinking && index == 0 ? 0.78 : 1)
            let eyeRect = CGRect(x: x + glanceX - width / 2, y: faceY - height / 2,
                                 width: width, height: height)
            let eye = Path(roundedRect: eyeRect, cornerRadius: min(width, height) * 0.46)
            context.fill(Path(roundedRect: eyeRect.insetBy(dx: -0.009, dy: -0.009),
                              cornerRadius: min(width, height) * 0.5), with: .color(light.opacity(0.12)))
            context.fill(eye, with: .linearGradient(Gradient(colors: [.white, light]),
                         startPoint: CGPoint(x: x, y: eyeRect.minY),
                         endPoint: CGPoint(x: x, y: eyeRect.maxY)))
        }

        drawVisorMouth(in: &context, light: light, phase: phase, level: level, reduced: reduced)
        if mood == .attention {
            var brow = Path()
            brow.move(to: CGPoint(x: 0.354, y: 0.354))
            brow.addQuadCurve(to: CGPoint(x: 0.418, y: 0.349), control: CGPoint(x: 0.381, y: 0.338))
            context.stroke(brow, with: .color(light.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 0.009, lineCap: .round))
        }
        // A small light inside the brass clasp is Jarvis's characteristic
        // intelligence beacon; all geometry remains inside the bundled body.
        let pulse = reduced || mood == .resting ? 0.65 : 0.65 + sin(phase * 3) * 0.25
        var beacon = Path()
        beacon.addLines([CGPoint(x: 0.499, y: 0.627), CGPoint(x: 0.509, y: 0.639),
                         CGPoint(x: 0.499, y: 0.651), CGPoint(x: 0.489, y: 0.639)])
        beacon.closeSubpath()
        context.fill(beacon, with: .color(light.opacity(pulse)))
    }

    private func drawVisorMouth(in context: inout GraphicsContext, light: Color,
                                phase: Double, level: Double, reduced: Bool) {
        switch mood {
        case .speaking:
            for index in 0..<3 {
                let wave = reduced ? 0.5 : abs(sin(phase * 7 + Double(index) * 0.9))
                let height = 0.012 + wave * (0.018 + level * 0.012)
                context.fill(Path(roundedRect: CGRect(x: 0.469 + Double(index) * 0.025,
                                                       y: 0.498 - height / 2, width: 0.012, height: height),
                                  cornerRadius: 0.006), with: .color(light.opacity(0.9)))
            }
        case .thinking:
            for index in 0..<3 {
                let brightness = reduced ? 0.65 : 0.35 + 0.65 * (0.5 + 0.5 * sin(phase * 4 - Double(index)))
                context.fill(Path(ellipseIn: CGRect(x: 0.472 + Double(index) * 0.023,
                                                   y: 0.493, width: 0.012, height: 0.012)),
                             with: .color(light.opacity(brightness)))
            }
        case .attention:
            var mouth = Path()
            mouth.move(to: CGPoint(x: 0.474, y: 0.499))
            mouth.addLine(to: CGPoint(x: 0.526, y: 0.499))
            context.stroke(mouth, with: .color(light.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 0.011, lineCap: .round))
        case .resting, .listening:
            var smile = Path()
            smile.move(to: CGPoint(x: 0.455, y: 0.489))
            smile.addQuadCurve(to: CGPoint(x: 0.545, y: 0.489),
                               control: CGPoint(x: 0.5, y: 0.513 + (reduced ? 0 : level * 0.008)))
            context.stroke(smile, with: .color(light.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 0.011, lineCap: .round))
        }
    }
}
