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
    private var statusItem: NotchStatusItem?
    private var screenObserver: NSObjectProtocol?
    private let presentation = NotchPresentationState()
    private var layout: NotchLayout?
    private var collapseTask: Task<Void, Never>?
    private var hideTask: Task<Void, Never>?
    private var statusHovered = false
    private var panelHovered = false
    private var outsideClickMonitor: Any?
    private var localEventMonitor: Any?
    private var openWindow: ((String) -> Void)?

    private init() {}

    func show(model: MenuBarModel, openWindow: ((String) -> Void)? = nil) {
        self.model = model
        self.openWindow = openWindow
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
            statusItem?.close()
            statusItem = nil
            return
        }
        self.layout = layout
        if openWindow != nil, statusItem == nil {
            statusItem = NotchStatusItem(model: model, presentation: presentation,
                hoverChanged: { [weak self] hovered in self?.hoverChanged(hovered, fromStatusItem: true) },
                activate: { [weak self] in self?.togglePresentation() })
        }

        if let panel {
            panel.setFrame(layout.expandedFrame, display: true)
            if presentation.interaction.isExpanded { panel.orderFrontRegardless() }
            return
        }

        let content = NotchHostingView(rootView: NotchPresenceView(
            model: model, presentation: presentation, interactive: openWindow != nil,
            togglePinned: { [weak self] in self?.togglePinned() },
            dismiss: { [weak self] in self?.dismiss() },
            openWindow: { [weak self] id in self?.openScene(id) }
        ))
        content.hoverChanged = { [weak self] hovered in self?.hoverChanged(hovered, fromStatusItem: false) }
        content.wantsLayer = true

        let panel = NotchPanel(
            contentRect: layout.expandedFrame,
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
        if openWindow != nil { installEventMonitors() }
        if openWindow == nil {
            // Display-only debug fixtures don't create a second status item.
            presentation.interaction.togglePinned()
            panel.orderFrontRegardless()
        } else {
            panel.alphaValue = presentation.interaction.isExpanded ? 1 : 0
            if presentation.interaction.isExpanded { panel.orderFrontRegardless() }
            else { panel.orderOut(nil) }
        }
    }

    func togglePresentation() {
        guard openWindow != nil else { return }
        if presentation.interaction.isPinned { dismiss() }
        else { togglePinned() }
    }

    private func hoverChanged(_ hovered: Bool, fromStatusItem: Bool) {
        guard openWindow != nil else { return }
        if fromStatusItem { statusHovered = hovered }
        else { panelHovered = hovered }
        let hovered = statusHovered || panelHovered
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
        statusHovered = false
        panelHovered = false
        mutateInteraction { $0.dismiss() }
        panel?.resignKey()
    }

    private func openScene(_ id: String) {
        guard let openWindow else { return }
        dismiss()
        openWindow(id)
    }

    private func mutateInteraction(_ change: (inout NotchInteractionState) -> Void) {
        let wasExpanded = presentation.interaction.isExpanded
        let reduced = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        withAnimation(reduced ? nil : .easeInOut(duration: 0.22)) {
            change(&presentation.interaction)
        }
        guard wasExpanded != presentation.interaction.isExpanded, let panel else { return }
        hideTask?.cancel()
        hideTask = nil
        if presentation.interaction.isExpanded {
            // Even during animation the window never enters the menu-bar strip.
            if let layout { panel.setFrame(layout.expandedFrame, display: true) }
            panel.alphaValue = reduced ? 1 : 0
            panel.orderFrontRegardless()
        }
        let targetAlpha: CGFloat = presentation.interaction.isExpanded ? 1 : 0
        if reduced { panel.alphaValue = targetAlpha }
        else {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.22
                context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().alphaValue = targetAlpha
            }
        }
        if !presentation.interaction.isExpanded {
            if reduced { panel.orderOut(nil) }
            else {
                hideTask = Task { @MainActor [weak self] in
                    do { try await Task.sleep(for: .milliseconds(240)) }
                    catch { return }
                    guard let self, !self.presentation.interaction.isExpanded else { return }
                    self.panel?.orderOut(nil)
                    self.hideTask = nil
                }
            }
        }
    }

    private func installEventMonitors() {
        // Mouse clicks only; no global keyboard monitoring or new permissions.
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) {
            [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let panel = self.panel,
                      !panel.frame.contains(NSEvent.mouseLocation),
                      !(self.statusItem?.containsScreenPoint(NSEvent.mouseLocation) ?? false) else { return }
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
                } else if event.window !== self.panel,
                          !(self.statusItem?.contains(event) ?? false) { self.dismiss() }
                return false
            }
            return consumed ? nil : event
        }
    }

    private func removePanel() {
        collapseTask?.cancel()
        collapseTask = nil
        hideTask?.cancel()
        hideTask = nil
        statusHovered = false
        panelHovered = false
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
        statusItem?.close()
        statusItem = nil
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        screenObserver = nil
        model = nil
        openWindow = nil
    }

}

fileprivate struct NotchLayout {
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
            return NotchLayout(expandedFrame: AssistantPanelLayout.expandedNotchFrame(
                geometry, screen: screen.frame, scale: screen.backingScaleFactor,
                menuBarHeight: max(screen.safeAreaInsets.top, screen.frame.maxY - screen.visibleFrame.maxY)))
        }
        return nil
    }
}

private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
