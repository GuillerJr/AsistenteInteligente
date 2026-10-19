import SwiftUI

/// Inverted shoulders join the display edge; only the lower corners are convex.
/// A floating rounded rectangle cannot reproduce the camera cutout's silhouette.
struct NotchOutline: Shape {
    var topInset: CGFloat
    var bottomRadius: CGFloat

    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { .init(topInset, bottomRadius) }
        set { topInset = newValue.first; bottomRadius = newValue.second }
    }

    func path(in rect: CGRect) -> Path {
        let shoulder = max(0, min(topInset, rect.width / 4, rect.height / 3))
        let radius = max(0, min(bottomRadius, (rect.width - 2 * shoulder) / 2,
                                rect.height - shoulder))
        let left = rect.minX + shoulder
        let right = rect.maxX - shoulder
        let top = rect.minY
        let bottom = rect.maxY
        let arc: CGFloat = 0.55228475

        return Path { path in
            path.move(to: CGPoint(x: rect.minX, y: top))
            path.addLine(to: CGPoint(x: rect.maxX, y: top))
            path.addCurve(to: CGPoint(x: right, y: top + shoulder),
                          control1: CGPoint(x: rect.maxX - shoulder * arc, y: top),
                          control2: CGPoint(x: right, y: top + shoulder * (1 - arc)))
            path.addLine(to: CGPoint(x: right, y: bottom - radius))
            path.addCurve(to: CGPoint(x: right - radius, y: bottom),
                          control1: CGPoint(x: right, y: bottom - radius * (1 - arc)),
                          control2: CGPoint(x: right - radius * (1 - arc), y: bottom))
            path.addLine(to: CGPoint(x: left + radius, y: bottom))
            path.addCurve(to: CGPoint(x: left, y: bottom - radius),
                          control1: CGPoint(x: left + radius * (1 - arc), y: bottom),
                          control2: CGPoint(x: left, y: bottom - radius * (1 - arc)))
            path.addLine(to: CGPoint(x: left, y: top + shoulder))
            path.addCurve(to: CGPoint(x: rect.minX, y: top),
                          control1: CGPoint(x: left, y: top + shoulder * (1 - arc)),
                          control2: CGPoint(x: rect.minX + shoulder * arc, y: top))
            path.closeSubpath()
        }
    }
}
