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
        // This is the physical camera exclusion, never an overlay on the two
        // usable menu-bar regions. Both presentation sizes extend below it.
        guard width > 0, height > 0 else { return nil }
        let center = (left.maxX + right.minX) / 2
        return Notch(frame: CGRect(x: aligned(center - width / 2), y: aligned(screen.maxY - height),
                                   width: width, height: height), width: width, height: height)
    }

    /// The compact extension occupies no menu-bar space and adds no status item.
    public static func compactNotchFrame(_ notch: Notch, screen: CGRect, scale: CGFloat,
                                         menuBarHeight: CGFloat = 0) -> CGRect {
        guard scale.isFinite, scale > 0, screen.contains(notch.frame) else { return .zero }
        let top = notchContentTop(notch, screen: screen, scale: scale, menuBarHeight: menuBarHeight)
        let height = min(CGFloat(24), max(0, top - screen.minY))
        return CGRect(x: notch.frame.minX, y: top - height, width: notch.width, height: height)
    }

    /// The interactive window stays entirely below both the camera and menu bar.
    public static func expandedNotchFrame(_ notch: Notch, screen: CGRect, scale: CGFloat,
                                          menuBarHeight: CGFloat = 0) -> CGRect {
        guard scale.isFinite, scale > 0, screen.contains(notch.frame) else { return .zero }
        func aligned(_ value: CGFloat) -> CGFloat { (value * scale).rounded() / scale }
        let reservedTop = menuBarHeight.isFinite ? max(notch.height, menuBarHeight) : notch.height
        let width = aligned(min(max(380, notch.frame.width), screen.width - 32))
        let height = aligned(max(0, min(176, screen.height - reservedTop - 16)))
        let top = notchContentTop(notch, screen: screen, scale: scale, menuBarHeight: menuBarHeight)
        return CGRect(x: min(max(aligned(notch.frame.midX - width / 2), screen.minX), screen.maxX - width),
                      y: top - height, width: width, height: height)
    }

    private static func notchContentTop(_ notch: Notch, screen: CGRect, scale: CGFloat,
                                        menuBarHeight: CGFloat) -> CGFloat {
        let reservedTop = menuBarHeight.isFinite ? max(notch.height, menuBarHeight) : notch.height
        // Round away from the menu bar when its edge lies between Retina pixels.
        return ((screen.maxY - reservedTop) * scale).rounded(.down) / scale
    }
}
