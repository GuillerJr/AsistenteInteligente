import AppKit
import SwiftUI

/// AppKit reserves this item's slot and lays out other apps beside it.
/// Placement is system-managed and supports the normal Command-drag gesture.
@MainActor
final class NotchStatusItem: NSObject {
    private let item: NSStatusItem
    private let activate: () -> Void

    init(model: MenuBarModel, presentation: NotchPresentationState,
         hoverChanged: @escaping (Bool) -> Void, activate: @escaping () -> Void) {
        self.activate = activate
        item = NSStatusBar.system.statusItem(withLength: 36)
        super.init()
        item.autosaveName = "Jarvis.InteractiveNotch"
        guard let button = item.button else { return }
        button.target = self
        button.action = #selector(pressed)
        button.toolTip = "Jarvis interactivo · acerca el cursor o pulsa para fijar el panel"
        button.setAccessibilityLabel("Jarvis interactivo")
        button.setAccessibilityHelp("Panel del notch. Pulsa para abrir y fijar. Escape o clic fuera lo cierra.")

        let content = NotchStatusHostingView(rootView: NotchStatusDot(model: model, presentation: presentation))
        content.frame = button.bounds
        content.autoresizingMask = [.width, .height]
        content.hoverChanged = hoverChanged
        button.addSubview(content)
    }

    @objc private func pressed() { activate() }

    func contains(_ event: NSEvent) -> Bool {
        guard let button = item.button, event.window === button.window else { return false }
        return button.bounds.contains(button.convert(event.locationInWindow, from: nil))
    }

    func containsScreenPoint(_ point: CGPoint) -> Bool {
        guard let button = item.button, let window = button.window else { return false }
        return window.convertToScreen(button.convert(button.bounds, to: nil)).contains(point)
    }

    func close() { NSStatusBar.system.removeStatusItem(item) }
}

private struct NotchStatusDot: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState

    var body: some View {
        JarvisNotchDot(color: model.presentationStatus.tone.color,
                       mood: .init(kind: model.presentationStatus.kind),
                       energy: Double(model.voiceActivityLevel),
                       engaged: presentation.interaction.isHovered || presentation.interaction.isPinned)
            .frame(width: 22, height: 22)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(.clear)
            .accessibilityHidden(true)
    }
}

private final class NotchStatusHostingView<Content: View>: NSHostingView<Content> {
    var hoverChanged: ((Bool) -> Void)?
    private var hoverArea: NSTrackingArea?

    override var isOpaque: Bool { false }
    // Leave clicks, highlighting and Command-drag placement to NSStatusBarButton.
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let hoverArea { removeTrackingArea(hoverArea) }
        let area = NSTrackingArea(rect: .zero,
                                 options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                 owner: self, userInfo: nil)
        addTrackingArea(area)
        hoverArea = area
    }

    override func mouseEntered(with event: NSEvent) { hoverChanged?(true) }
    override func mouseExited(with event: NSEvent) { hoverChanged?(false) }
}
