import AppKit
import SwiftUI

@MainActor
final class NotchPanelController {
    static let shared = NotchPanelController()

    private var model: MenuBarModel?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?

    private init() {}

    func show(model: MenuBarModel) {
        self.model = model
        if screenObserver == nil {
            screenObserver = NotificationCenter.default.addObserver(
                forName: NSApplication.didChangeScreenParametersNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.reconcile()
                }
            }
        }
        reconcile()
    }

    private func reconcile() {
        guard let model, let layout = NotchLayout.current() else {
            panel?.orderOut(nil)
            panel = nil
            return
        }

        panel?.orderOut(nil)
        let panel = NotchPanel(
            contentRect: layout.panelFrame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "Presencia de Jarvis"
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.isRestorable = false
        panel.ignoresMouseEvents = true
        panel.animationBehavior = .none
        panel.level = .statusBar
        panel.collectionBehavior = [
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
            .ignoresCycle,
            .stationary,
        ]
        panel.contentView = NSHostingView(
            rootView: NotchPresenceView(
                model: model,
                notchWidth: layout.notchWidth,
                notchHeight: layout.notchHeight
            )
        )
        self.panel = panel
        panel.orderFrontRegardless()
    }
}

private struct NotchLayout {
    let panelFrame: NSRect
    let notchWidth: CGFloat
    let notchHeight: CGFloat

    static func current() -> NotchLayout? {
        for screen in NSScreen.screens {
            guard
                screen.safeAreaInsets.top > 0,
                let left = screen.auxiliaryTopLeftArea,
                let right = screen.auxiliaryTopRightArea,
                !left.isEmpty,
                !right.isEmpty
            else {
                continue
            }
            let notchWidth = right.minX - left.maxX
            guard notchWidth > 0 else { continue }

            let notchHeight = screen.safeAreaInsets.top
            let wingWidth: CGFloat = 28
            let panelHeight = notchHeight + 8
            let centerX = (left.maxX + right.minX) / 2
            return NotchLayout(
                panelFrame: NSRect(
                    x: centerX - ((notchWidth + (wingWidth * 2)) / 2),
                    y: screen.frame.maxY - panelHeight,
                    width: notchWidth + (wingWidth * 2),
                    height: panelHeight
                ),
                notchWidth: notchWidth,
                notchHeight: notchHeight
            )
        }
        return nil
    }
}

private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
