import AppKit
import QuartzCore
import SwiftUI

@MainActor
@Observable
final class NotchPresentationState {
    var expanded = false
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
    private var reviewApproval: (() -> Void)?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?
    private let presentation = NotchPresentationState()

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
                    self?.reconcile(animated: false)
                }
            }
        }
        reconcile(animated: false)
    }

    private func reconcile(animated: Bool) {
        guard let model, let layout = NotchLayout.current(expanded: presentation.expanded) else {
            panel?.orderOut(nil)
            panel = nil
            presentation.expanded = false
            return
        }
        presentation.update(from: layout)

        if let panel {
            panel.hasShadow = presentation.expanded
            setFrame(layout.panelFrame, on: panel, animated: animated)
            panel.orderFrontRegardless()
            return
        }

        let content = NSHostingView(
            rootView: NotchPresenceView(
                model: model,
                presentation: presentation,
                toggle: { [weak self] in self?.toggle() },
                startVoiceTurn: { [weak self] in self?.startVoiceTurn() },
                configureVoice: { [weak self] in self?.configureVoice() },
                reviewApproval: { [weak self] in self?.showApproval() },
                showHUD: { [weak self] in self?.showHUD() }
            )
        )
        content.wantsLayer = true

        let panel = NotchPanel(
            contentRect: layout.panelFrame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "Controles de Jarvis"
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = presentation.expanded
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
        setExpanded(!presentation.expanded)
    }

    private func startVoiceTurn() {
        guard let model, model.canStartVoiceTurn else { return }
        setExpanded(false)
        Task { await model.startVoiceTurn() }
    }

    private func configureVoice() {
        guard let model else { return }
        setExpanded(false)

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
        setExpanded(false)
        reviewApproval?()
    }

    private func showHUD() {
        guard let model else { return }
        setExpanded(false)
        HUDPanelController.shared.show(model: model)
    }

    private func setExpanded(_ expanded: Bool) {
        guard presentation.expanded != expanded else { return }
        let reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        let animation: Animation = reduceMotion
            ? .easeOut(duration: 0.12)
            : .spring(response: 0.34, dampingFraction: 0.86, blendDuration: 0.08)
        withAnimation(animation) {
            presentation.expanded = expanded
        }
        reconcile(animated: true)
    }

    private func setFrame(_ frame: NSRect, on panel: NSPanel, animated: Bool) {
        guard animated, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else {
            panel.setFrame(frame, display: true)
            return
        }
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.32
            context.timingFunction = CAMediaTimingFunction(
                controlPoints: 0.22,
                0.82,
                0.32,
                1
            )
            panel.animator().setFrame(frame, display: true)
        }
    }
}

fileprivate struct NotchLayout {
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
