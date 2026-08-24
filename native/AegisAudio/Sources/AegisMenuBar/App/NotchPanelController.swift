import AppKit
import SwiftUI

@MainActor
@Observable
final class NotchPresentationState {
    var notchWidth: CGFloat = 0
    var notchHeight: CGFloat = 0
    var wingWidth: CGFloat = 0

    fileprivate func update(from layout: NotchLayout) {
        notchWidth = layout.notchWidth
        notchHeight = layout.notchHeight
        wingWidth = layout.wingWidth
    }
}

@MainActor
final class NotchPanelController {
    static let shared = NotchPanelController()

    private var model: MenuBarModel?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?
    private let presentation = NotchPresentationState()

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
        presentation.update(from: layout)

        if let panel {
            panel.setFrame(layout.panelFrame, display: true)
            panel.orderFrontRegardless()
            return
        }

        let content = NSHostingView(
            rootView: NotchPresenceView(model: model, presentation: presentation)
        )
        content.wantsLayer = true

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
        panel.contentView = content
        self.panel = panel
        panel.orderFrontRegardless()
    }
}

fileprivate struct NotchLayout {
    let panelFrame: NSRect
    let notchWidth: CGFloat
    let notchHeight: CGFloat
    let wingWidth: CGFloat

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
            let scale = screen.backingScaleFactor
            let notchWidth = aligned(right.minX - left.maxX, scale: scale)
            guard notchWidth > 0 else { continue }

            let notchHeight = aligned(screen.safeAreaInsets.top, scale: scale)
            let wingWidth: CGFloat = 34
            let desiredWidth = notchWidth + 144
            let panelWidth = aligned(
                min(max(notchWidth + (wingWidth * 2), desiredWidth), screen.frame.width - 32),
                scale: scale
            )
            let panelHeight = aligned(notchHeight + 62, scale: scale)
            let centerX = aligned((left.maxX + right.minX) / 2, scale: scale)
            return NotchLayout(
                panelFrame: NSRect(
                    x: aligned(centerX - (panelWidth / 2), scale: scale),
                    y: aligned(screen.frame.maxY - panelHeight, scale: scale),
                    width: panelWidth,
                    height: panelHeight
                ),
                notchWidth: notchWidth,
                notchHeight: notchHeight,
                wingWidth: wingWidth
            )
        }
        return nil
    }

    private static func aligned(_ value: CGFloat, scale: CGFloat) -> CGFloat {
        (value * scale).rounded() / scale
    }
}

private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
