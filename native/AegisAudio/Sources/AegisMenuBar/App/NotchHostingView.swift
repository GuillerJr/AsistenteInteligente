import AppKit
import SwiftUI

/// Tracks only the notch's actual window; no desktop polling or event taps.
final class NotchHostingView<Content: View>: NSHostingView<Content> {
    var hoverChanged: ((Bool) -> Void)?
    var pointerMoved: ((Double, Double) -> Void)?
    private var hoverArea: NSTrackingArea?
    private var lastPointerTime = -Double.infinity

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        // .inVisibleRect follows resizing automatically. Replacing the area on
        // every animation frame can manufacture exit/entry events and flicker.
        guard hoverArea == nil else { return }
        let area = NSTrackingArea(rect: .zero,
                                 options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways, .inVisibleRect],
                                 owner: self, userInfo: nil)
        addTrackingArea(area)
        hoverArea = area
    }

    override func mouseEntered(with event: NSEvent) { hoverChanged?(true) }
    override func mouseExited(with event: NSEvent) { hoverChanged?(false) }

    override func mouseMoved(with event: NSEvent) {
        // Coalesce high-frequency pointer events; gaze interpolation uses the artwork clock.
        guard event.timestamp - lastPointerTime >= 1 / 30 else { return }
        lastPointerTime = event.timestamp
        let location = convert(event.locationInWindow, from: nil)
        let x = (location.x - bounds.midX) / max(bounds.width / 2, 1)
        let y = (location.y - bounds.midY) / max(bounds.height / 2, 1)
        pointerMoved?(Double(x), Double(isFlipped ? y : -y))
    }
}
