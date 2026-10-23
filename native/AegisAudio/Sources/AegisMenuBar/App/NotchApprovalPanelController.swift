import AppKit
import AegisAudioCore
import SwiftUI

/// Decisions for requests made by voice or to Dot appear just under the notch, like
/// a live activity: a small card with the buttons, no modal window and no activation
/// of Jarvis over the app the owner is using. Macs without a notch get it centred
/// under the menu bar.
@MainActor
final class NotchApprovalPanelController {
    static let shared = NotchApprovalPanelController()
    static let width: CGFloat = 400

    private var panel: NSPanel?
    private var host: NSHostingController<NotchDecisionContent>?
    private var sizeObservation: NSKeyValueObservation?
    private var shownInteraction: String?

    private init() {}

    func show(model: MenuBarModel) {
        guard let interaction = model.interactionID else { return hide() }
        guard shownInteraction != interaction || panel?.isVisible != true else { return }
        shownInteraction = interaction
        let host = NSHostingController(rootView: NotchDecisionContent(model: model))
        // The card reports its own height, also when its details are expanded.
        host.sizingOptions = [.preferredContentSize]
        let panel = self.panel ?? Self.makePanel()
        panel.contentViewController = host
        self.host = host
        self.panel = panel
        sizeObservation = host.observe(\.preferredContentSize, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.place() }
        }
        place()
        panel.orderFrontRegardless()
    }

    func hide() {
        shownInteraction = nil
        sizeObservation = nil
        panel?.orderOut(nil)
        panel?.contentViewController = nil
        host = nil
    }

    /// Keeps the card's top edge fixed under Dot while its height changes.
    private func place() {
        guard let panel, let host else { return }
        let fitted = host.sizeThatFits(in: CGSize(width: Self.width, height: 10_000))
        let size = NSSize(width: Self.width, height: ceil(max(80, fitted.height)))
        panel.setFrame(Self.frame(for: size), display: true)
    }

    private static func makePanel() -> NSPanel {
        let panel = DecisionPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                                  backing: .buffered, defer: false)
        panel.title = String(localized: "Decisión de Jarvis", bundle: .module)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        // Black like the notch it hangs from; the theme colours follow the appearance.
        panel.appearance = NSAppearance(named: .darkAqua)
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.isRestorable = false
        panel.isReleasedWhenClosed = false
        panel.animationBehavior = .utilityWindow
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        return panel
    }

    /// Centred under Dot's presence at the notch, or under the menu bar of the main
    /// screen when there is no notch.
    static func frame(for size: NSSize) -> NSRect {
        let screen = NSScreen.screens.first { $0.safeAreaInsets.top > 0 } ?? NSScreen.main
        guard let screen else { return NSRect(origin: .zero, size: size) }
        let menuBar = max(screen.safeAreaInsets.top, screen.frame.maxY - screen.visibleFrame.maxY)
        var top = screen.frame.maxY - menuBar
        if let presence = NSApp.windows.first(where: { $0.title == "Presencia de Jarvis" && $0.isVisible }),
           screen.frame.intersects(presence.frame) {
            top = min(top, presence.frame.minY)
        }
        return NSRect(x: screen.frame.midX - size.width / 2, y: top - 8 - size.height,
                      width: size.width, height: size.height)
    }
}

/// Takes keyboard focus only when the owner clicks its answer field, without
/// bringing Jarvis in front of the app they are using.
private final class DecisionPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

struct NotchDecisionContent: View {
    let model: MenuBarModel

    var body: some View {
        JarvisDecisionCard(model: model, style: .notch)
            .frame(width: NotchApprovalPanelController.width)
            .fixedSize(horizontal: false, vertical: true)
            .environment(\.colorScheme, .dark)
    }
}
