import AppKit
import SwiftUI

/// Tracks only the notch's actual window; no desktop polling or event taps.
final class NotchHostingView<Content: View>: NSHostingView<Content> {
    var hoverChanged: ((Bool) -> Void)?
    private var hoverArea: NSTrackingArea?

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        // .inVisibleRect follows resizing automatically. Replacing the area on
        // every animation frame can manufacture exit/entry events and flicker.
        guard hoverArea == nil else { return }
        let area = NSTrackingArea(rect: .zero,
                                 options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                 owner: self, userInfo: nil)
        addTrackingArea(area)
        hoverArea = area
    }

    override func mouseEntered(with event: NSEvent) { hoverChanged?(true) }
    override func mouseExited(with event: NSEvent) { hoverChanged?(false) }
}
