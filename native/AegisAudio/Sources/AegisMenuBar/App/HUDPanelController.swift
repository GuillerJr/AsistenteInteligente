import AppKit
import AegisAudioCore
import SwiftUI

@MainActor
final class HUDPanelController {
    static let shared = HUDPanelController()

    private var panel: HUDPanel?
    private var screenObserver: NSObjectProtocol?

    private init() {}

    func show(model: MenuBarModel) {
        if let panel {
            reconcileFrame()
            panel.makeKeyAndOrderFront(nil)
            return
        }

        let panel = HUDPanel(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 560),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "HUD táctico de Jarvis"
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isMovableByWindowBackground = true
        panel.isRestorable = false
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        var interactive = true
#if DEBUG
        interactive = NotchPreviewMode(arguments: ProcessInfo.processInfo.arguments) == nil
#endif
        panel.contentView = NSHostingView(
            rootView: HUDView(model: model, close: { [weak self] in
                self?.close()
            }, interactive: interactive)
        )
        self.panel = panel
        reconcileFrame(center: true)
        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.reconcileFrame() }
        }

        panel.makeKeyAndOrderFront(nil)
    }

    private func reconcileFrame(center: Bool = false) {
        guard let panel else { return }
        let screens = NSScreen.screens
        let screen = center ? screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) })
            : screens.first(where: { $0.frame.intersects(panel.frame) })
        guard let target = screen ?? NSScreen.main ?? screens.first else { return }
        panel.setFrame(AssistantPanelLayout.hudFrame(
            visibleFrame: target.visibleFrame, previousFrame: center ? nil : panel.frame
        ), display: true)
    }

    func close() {
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        screenObserver = nil
        panel?.orderOut(nil)
        panel?.contentView = nil
        panel = nil
    }
}

private final class HUDPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func cancelOperation(_ sender: Any?) {
        HUDPanelController.shared.close()
    }
}
