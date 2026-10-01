import Foundation

/// Pure screen-space geometry, including negative-origin secondary displays.
public enum AssistantPanelLayout {
    public static let hudPreferredSize = CGSize(width: 460, height: 480)

    public static func hudFrame(visibleFrame: CGRect, previousFrame: CGRect? = nil) -> CGRect {
        let bounds = visibleFrame.insetBy(dx: min(16, visibleFrame.width / 4),
                                         dy: min(16, visibleFrame.height / 4))
        let size = CGSize(width: min(hudPreferredSize.width, bounds.width),
                          height: min(hudPreferredSize.height, bounds.height))
        let origin = previousFrame?.origin ?? CGPoint(x: bounds.midX - size.width / 2,
                                                      y: bounds.midY - size.height / 2)
        return CGRect(x: min(max(origin.x, bounds.minX), bounds.maxX - size.width),
                      y: min(max(origin.y, bounds.minY), bounds.maxY - size.height),
                      width: size.width, height: size.height)
    }

    public struct Notch: Equatable, Sendable {
        public let frame: CGRect
        public let width: CGFloat
        public let height: CGFloat
        public var wingWidth: CGFloat { (frame.width - width) / 2 }
    }

    public static func notch(screen: CGRect, left: CGRect, right: CGRect,
                             safeTop: CGFloat, scale: CGFloat) -> Notch? {
        guard scale.isFinite, scale > 0, safeTop.isFinite, safeTop > 0,
              screen.width.isFinite, screen.height.isFinite, screen.width > 32,
              screen.height > safeTop, !left.isEmpty, !right.isEmpty,
              screen.contains(left), screen.contains(right), right.minX > left.maxX else { return nil }
        func aligned(_ value: CGFloat) -> CGFloat { (value * scale).rounded() / scale }
        let width = aligned(right.minX - left.maxX)
        let height = aligned(safeTop)
        // Keep the entire presence in the camera's top strip. Only the two
        // visible side areas contain UI; the physical camera gap stays empty.
        let wing = aligned(min(64, left.width - 16, right.width - 16))
        let panelWidth = width + 2 * wing
        guard width > 0, height > 0, wing > 0 else { return nil }
        let center = (left.maxX + right.minX) / 2
        let x = min(max(aligned(center - panelWidth / 2), screen.minX), screen.maxX - panelWidth)
        return Notch(frame: CGRect(x: x, y: aligned(screen.maxY - height),
                                   width: panelWidth, height: height), width: width, height: height)
    }

    /// Expands downward from the camera, without moving its center or top edge.
    public static func expandedNotchFrame(_ notch: Notch, screen: CGRect, scale: CGFloat) -> CGRect {
        guard scale.isFinite, scale > 0, screen.contains(notch.frame) else { return notch.frame }
        func aligned(_ value: CGFloat) -> CGFloat { (value * scale).rounded() / scale }
        let width = aligned(min(max(380, notch.frame.width), screen.width - 32))
        let height = aligned(min(notch.height + 176, screen.height - 16))
        return CGRect(x: min(max(aligned(notch.frame.midX - width / 2), screen.minX), screen.maxX - width),
                      y: aligned(notch.frame.maxY - height), width: width, height: height)
    }
}
