import AppKit
import SwiftUI

@MainActor
final class NotchPanelController {
    static let shared = NotchPanelController()

    private var model: MenuBarModel?
    private var reviewApproval: (() -> Void)?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?
    private var isExpanded = false

    private init() {}

    func show(model: MenuBarModel, reviewApproval: @escaping () -> Void) {
        self.model = model
        self.reviewApproval = reviewApproval
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
                wingWidth: layout.wingWidth,
                expanded: isExpanded,
                toggle: { [weak self] in self?.toggle() },
                startVoiceTurn: { [weak self] in self?.startVoiceTurn() },
                configureVoice: { [weak self] in self?.configureVoice() },
                reviewApproval: { [weak self] in self?.showApproval() },
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

    private func configureVoice() {
        guard let model else { return }
        isExpanded = false
        reconcile()

        switch model.microphonePermission {
        case .denied, .restricted:
            model.openMicrophoneSettings()
            return
        case .authorized, .notDetermined, .unknown:
            break
        }
        switch model.speechPermission {
        case .denied, .restricted:
            model.openSpeechSettings()
            return
        case .authorized, .notDetermined, .unknown:
            break
        }
        if model.microphonePermission == .notDetermined
            || model.speechPermission == .notDetermined
        {
            Task { await model.requestUndeterminedPermissions() }
        }
    }

    private func showApproval() {
        guard let model, model.pendingApproval != nil else { return }
        isExpanded = false
        reconcile()
        reviewApproval?()
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
    let wingWidth: CGFloat

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
            let scale = screen.backingScaleFactor
            let notchWidth = aligned(right.minX - left.maxX, scale: scale)
            guard notchWidth > 0 else { continue }

            let notchHeight = aligned(screen.safeAreaInsets.top, scale: scale)
            let wingWidth: CGFloat = 28
            let collapsedWidth = notchWidth + (wingWidth * 2)
            let panelWidth = aligned(
                expanded ? max(collapsedWidth, 388) : collapsedWidth,
                scale: scale
            )
            let panelHeight = notchHeight + (expanded ? 172 : 8)
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
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
