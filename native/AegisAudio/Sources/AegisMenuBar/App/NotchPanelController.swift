import AppKit
import SwiftUI

@MainActor
final class NotchPanelController {
    static let shared = NotchPanelController()

    private var model: MenuBarModel?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?
    private var isExpanded = false

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
        guard let model, let layout = NotchLayout.current(expanded: isExpanded) else {
            panel?.orderOut(nil)
            panel = nil
            isExpanded = false
            return
        }

        let content = NSHostingView(
            rootView: NotchPresenceView(
                model: model,
                notchWidth: layout.notchWidth,
                notchHeight: layout.notchHeight,
                expanded: isExpanded,
                toggle: { [weak self] in self?.toggle() },
                startVoiceTurn: { [weak self] in self?.startVoiceTurn() },
                showHUD: { [weak self] in self?.showHUD() }
            )
        )
        if let panel {
            panel.hasShadow = isExpanded
            panel.setFrame(layout.panelFrame, display: true, animate: true)
            panel.contentView = content
            panel.orderFrontRegardless()
            return
        }

        let panel = NotchPanel(
            contentRect: layout.panelFrame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "Controles de Jarvis"
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = isExpanded
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.isRestorable = false
        panel.ignoresMouseEvents = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.animationBehavior = .none
        panel.level = .statusBar
        panel.collectionBehavior = [
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
            .ignoresCycle,
            .stationary,
        ]
        panel.contentView = content
        self.panel = panel
        panel.orderFrontRegardless()
    }

    private func toggle() {
        isExpanded.toggle()
        reconcile()
    }

    private func startVoiceTurn() {
        guard let model, model.canStartVoiceTurn else { return }
        isExpanded = false
        reconcile()
        Task { await model.startVoiceTurn() }
    }

    private func showHUD() {
        guard let model else { return }
        isExpanded = false
        reconcile()
        HUDPanelController.shared.show(model: model)
    }
}

private struct NotchLayout {
    let panelFrame: NSRect
    let notchWidth: CGFloat
    let notchHeight: CGFloat

    static func current(expanded: Bool) -> NotchLayout? {
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
            let collapsedWidth = notchWidth + (wingWidth * 2)
            let panelWidth = expanded ? max(collapsedWidth, 388) : collapsedWidth
            let panelHeight = notchHeight + (expanded ? 172 : 8)
            let centerX = (left.maxX + right.minX) / 2
            return NotchLayout(
                panelFrame: NSRect(
                    x: centerX - (panelWidth / 2),
                    y: screen.frame.maxY - panelHeight,
                    width: panelWidth,
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
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
