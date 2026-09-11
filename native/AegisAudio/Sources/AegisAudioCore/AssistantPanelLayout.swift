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
    }

    public static func notch(screen: CGRect, left: CGRect, right: CGRect,
                             safeTop: CGFloat, scale: CGFloat) -> Notch? {
        guard scale.isFinite, scale > 0, safeTop.isFinite, safeTop > 0,
              screen.width.isFinite, screen.height.isFinite, screen.width > 32,
              screen.height > safeTop + 62, !left.isEmpty, !right.isEmpty,
              screen.contains(left), screen.contains(right), right.minX > left.maxX else { return nil }
        func aligned(_ value: CGFloat) -> CGFloat { (value * scale).rounded() / scale }
        let width = aligned(right.minX - left.maxX)
        let height = aligned(safeTop)
        let panelWidth = aligned(min(width + 144, screen.width - 32))
        guard width > 0, height > 0, panelWidth >= width else { return nil }
        let center = (left.maxX + right.minX) / 2
        let x = min(max(aligned(center - panelWidth / 2), screen.minX), screen.maxX - panelWidth)
        return Notch(frame: CGRect(x: x, y: aligned(screen.maxY - height - 62),
                                   width: panelWidth, height: height + 62), width: width, height: height)
    }
}
