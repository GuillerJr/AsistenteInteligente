import SwiftUI

struct JarvisMenuBarIcon: View {
    var body: some View {
        Canvas { context, size in
            let scale = min(size.width, size.height) / 18
            let foreground = GraphicsContext.Shading.color(.primary)
            let secondary = GraphicsContext.Shading.color(.primary.opacity(0.72))

            var sphere = Path()
            sphere.addEllipse(
                in: CGRect(x: 2.5 * scale, y: 2.5 * scale, width: 13 * scale, height: 13 * scale)
            )
            context.stroke(
                sphere,
                with: secondary,
                style: StrokeStyle(lineWidth: 1.1 * scale)
            )

            var network = Path()
            network.move(to: point(4.7, 6.2, scale: scale))
            network.addLine(to: point(8.1, 4.1, scale: scale))
            network.addLine(to: point(12.8, 6.0, scale: scale))
            network.move(to: point(4.7, 6.2, scale: scale))
            network.addLine(to: point(5.4, 11.9, scale: scale))
            network.addLine(to: point(10.8, 10.4, scale: scale))
            network.addLine(to: point(12.8, 6.0, scale: scale))
            context.stroke(
                network,
                with: secondary,
                style: StrokeStyle(lineWidth: 0.8 * scale, lineCap: .round, lineJoin: .round)
            )

            var letter = Path()
            letter.move(to: point(10.8, 4.0, scale: scale))
            letter.addLine(to: point(10.8, 10.0, scale: scale))
            letter.addCurve(
                to: point(5.5, 11.8, scale: scale),
                control1: point(10.8, 13.0, scale: scale),
                control2: point(7.0, 14.2, scale: scale)
            )
            context.stroke(
                letter,
                with: foreground,
                style: StrokeStyle(lineWidth: 1.7 * scale, lineCap: .round, lineJoin: .round)
            )

            for node in [(4.7, 6.2), (8.1, 4.1), (12.8, 6.0), (5.5, 11.8)] {
                let center = point(node.0, node.1, scale: scale)
                let radius = 1.05 * scale
                let circle = Path(
                    ellipseIn: CGRect(
                        x: center.x - radius,
                        y: center.y - radius,
                        width: radius * 2,
                        height: radius * 2
                    )
                )
                context.fill(circle, with: foreground)
            }
        }
        .accessibilityHidden(true)
    }

    private func point(_ x: CGFloat, _ y: CGFloat, scale: CGFloat) -> CGPoint {
        CGPoint(x: x * scale, y: y * scale)
    }
}
