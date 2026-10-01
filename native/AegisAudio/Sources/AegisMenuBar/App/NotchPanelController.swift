import AppKit
import AegisAudioCore
import SwiftUI
import QuartzCore

@MainActor
@Observable
final class NotchPresentationState {
    var interaction = NotchInteractionState()
}

@MainActor
final class NotchPanelController {
    static let shared = NotchPanelController()

    private var model: MenuBarModel?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?
    private let presentation = NotchPresentationState()
    private var layout: NotchLayout?
    private var collapseTask: Task<Void, Never>?
    private var outsideClickMonitor: Any?
    private var localEventMonitor: Any?
    private var interactive = false

    private init() {}

    func show(model: MenuBarModel, interactive: Bool = true) {
        self.model = model
        self.interactive = interactive
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
            removePanel()
            return
        }
        self.layout = layout

        if let panel {
            panel.setFrame(currentFrame, display: true)
            panel.orderFrontRegardless()
            return
        }

        let content = NotchHostingView(rootView: NotchPresenceView(
            model: model, presentation: presentation, interactive: interactive,
            togglePinned: { [weak self] in self?.togglePinned() },
            dismiss: { [weak self] in self?.dismiss() }
        ))
        // The controller owns both sizes; content changes must not resize the
        // panel through intrinsic sizing while its frame is being animated.
        content.sizingOptions = []
        content.hoverChanged = { [weak self] hovered in self?.hoverChanged(hovered) }
        content.wantsLayer = true

        let panel = NotchPanel(
            contentRect: currentFrame,
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
        panel.isReleasedWhenClosed = false
        panel.ignoresMouseEvents = false
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
        if interactive { installEventMonitors() }
        if !interactive {
            // Display-only debug fixtures show the expanded layout.
            presentation.interaction.togglePinned()
            panel.setFrame(layout.expandedFrame, display: true)
            panel.orderFrontRegardless()
        } else {
            panel.orderFrontRegardless()
        }
    }

    private var currentFrame: CGRect {
        guard let layout else { return .zero }
        return presentation.interaction.isExpanded ? layout.expandedFrame : layout.compactFrame
    }

    func togglePresentation() {
        guard interactive else { return }
        if presentation.interaction.isPinned { dismiss() }
        else { togglePinned() }
    }

    private func hoverChanged(_ hovered: Bool) {
        guard interactive else { return }
        collapseTask?.cancel()
        mutateInteraction { $0.setHovered(hovered) }
        if !hovered {
            collapseTask = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .milliseconds(350)) }
                catch { return }
                self?.mutateInteraction { $0.collapseIfUnhovered() }
                self?.collapseTask = nil
            }
        }
    }

    private func togglePinned() {
        collapseTask?.cancel()
        mutateInteraction { $0.togglePinned() }
        if presentation.interaction.isPinned { panel?.makeKey() }
    }

    private func dismiss() {
        guard presentation.interaction.isExpanded else { return }
        collapseTask?.cancel()
        collapseTask = nil
        mutateInteraction { $0.dismiss() }
        panel?.resignKey()
    }

    private func mutateInteraction(_ change: (inout NotchInteractionState) -> Void) {
        let wasExpanded = presentation.interaction.isExpanded
        let reduced = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        withAnimation(reduced ? nil : .easeInOut(duration: 0.22)) {
            change(&presentation.interaction)
        }
        guard wasExpanded != presentation.interaction.isExpanded, let panel else { return }
        // Both end frames share a top edge below the menu bar; interpolation
        // cannot cover its icons, including during the expansion animation.
        let frame = currentFrame
        if reduced { panel.setFrame(frame, display: true) }
        else {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.22
                context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().setFrame(frame, display: true)
            }
        }
    }

    private func installEventMonitors() {
        // Mouse clicks only; no global keyboard monitoring or new permissions.
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) {
            [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let panel = self.panel,
                      !panel.frame.contains(NSEvent.mouseLocation) else { return }
                self.dismiss()
            }
        }
        localEventMonitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown, .keyDown]) {
            [weak self] event in
            let consumed = MainActor.assumeIsolated {
                guard let self, self.presentation.interaction.isExpanded else { return false }
                if event.type == .keyDown {
                    if event.keyCode == 53, event.window === self.panel {
                        self.dismiss()
                        return true
                    }
                } else if event.window !== self.panel { self.dismiss() }
                return false
            }
            return consumed ? nil : event
        }
    }

    private func removePanel() {
        collapseTask?.cancel()
        collapseTask = nil
        if let outsideClickMonitor { NSEvent.removeMonitor(outsideClickMonitor) }
        if let localEventMonitor { NSEvent.removeMonitor(localEventMonitor) }
        outsideClickMonitor = nil
        localEventMonitor = nil
        panel?.orderOut(nil)
        panel?.contentView = nil
        panel = nil
        layout = nil
        presentation.interaction.dismiss()
    }

    func close() {
        removePanel()
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        screenObserver = nil
        model = nil
        interactive = false
    }

}

fileprivate struct NotchLayout {
    let compactFrame: NSRect
    let expandedFrame: NSRect

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
            guard let geometry = AssistantPanelLayout.notch(
                screen: screen.frame, left: left, right: right,
                safeTop: screen.safeAreaInsets.top, scale: screen.backingScaleFactor
            ) else { continue }
            let menuBarHeight = max(screen.safeAreaInsets.top, screen.frame.maxY - screen.visibleFrame.maxY)
            return NotchLayout(compactFrame: AssistantPanelLayout.compactNotchFrame(
                geometry, screen: screen.frame, scale: screen.backingScaleFactor, menuBarHeight: menuBarHeight),
                expandedFrame: AssistantPanelLayout.expandedNotchFrame(
                geometry, screen: screen.frame, scale: screen.backingScaleFactor,
                menuBarHeight: menuBarHeight))
        }
        return nil
    }
}

private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
