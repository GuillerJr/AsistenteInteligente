import AppKit
import AegisAudioCore
import SwiftUI

@MainActor
@Observable
final class NotchPresentationState {
    var interaction = NotchInteractionState()
    var expansionProgress: Double = 0
}

@MainActor
final class NotchPanelController {
    static let shared = NotchPanelController()

    private var model: MenuBarModel?
    private var panel: NotchPanel?
    private var screenObserver: NSObjectProtocol?
    private var accessibilityObserver: NSObjectProtocol?
    private let presentation = NotchPresentationState()
    private let expansionAnimator = NotchExpansionAnimator()
    private var layout: NotchLayout?
    private var collapseTask: Task<Void, Never>?
    private var outsideClickMonitor: Any?
    private var localEventMonitor: Any?
    private var interactive = false
    private var reviewInteraction: (() -> Void)?

    private init() {}

    func show(model: MenuBarModel, interactive: Bool = true, reviewInteraction: (() -> Void)? = nil) {
        self.model = model
        self.interactive = interactive
        self.reviewInteraction = reviewInteraction
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
        if accessibilityObserver == nil {
            accessibilityObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
                object: nil, queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self, NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
                    self.expansionAnimator.finish(expanded: self.presentation.interaction.isExpanded)
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
        expansionAnimator.finish(expanded: presentation.interaction.isExpanded)
        presentation.expansionProgress = presentation.interaction.isExpanded ? 1 : 0

        if let panel {
            panel.setFrame(currentFrame, display: true)
            panel.orderFrontRegardless()
            return
        }

        let content = NotchHostingView(rootView: NotchPresenceView(
            model: model, presentation: presentation, interactive: interactive,
            togglePinned: { [weak self] in self?.togglePinned() },
            keepOpen: { [weak self] in self?.keepOpen() },
            activateVoice: { [weak self] in self?.activateVoice() },
            reviewInteraction: { [weak self] in self?.reviewInteraction?() },
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
            presentation.expansionProgress = 1
            expansionAnimator.finish(expanded: true)
            panel.setFrame(layout.expandedFrame, display: true)
            panel.orderFrontRegardless()
        } else {
            panel.orderFrontRegardless()
        }
    }

    private var currentFrame: CGRect {
        guard let layout else { return .zero }
        return AssistantPanelLayout.interpolatedNotchFrame(
            compact: layout.compactFrame, expanded: layout.expandedFrame,
            progress: presentation.expansionProgress)
    }

    func togglePresentation() {
        guard interactive else { return }
        if presentation.interaction.isPinned { dismiss() }
        else { togglePinned() }
    }

    private func hoverChanged(_ hovered: Bool) {
        guard interactive, presentation.interaction.isHovered != hovered else { return }
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

    private func keepOpen() {
        guard interactive else { return }
        collapseTask?.cancel()
        if !presentation.interaction.isPinned { mutateInteraction { $0.togglePinned() } }
        panel?.makeKey()
    }

    private func activateVoice() {
        guard interactive, let model, model.mascotSessionAvailable else { return }
        if model.interactionID != nil {
            reviewInteraction?()
            return
        }
        guard model.canActivateMascotVoice else { return }
        keepOpen()
        Task { await model.startVoiceTurn() }
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
        change(&presentation.interaction)
        guard wasExpanded != presentation.interaction.isExpanded, let view = panel?.contentView else { return }
        expansionAnimator.move(expanded: presentation.interaction.isExpanded, in: view,
                               animated: !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion) {
            [weak self] progress in
            self?.renderExpansion(progress)
        }
    }

    private func renderExpansion(_ progress: Double) {
        guard let panel else { return }
        // No second implicit animation: the native window, outline, mascot and
        // label all use the display clock's current value in the same update.
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            presentation.expansionProgress = progress
            panel.setFrame(currentFrame, display: false)
            panel.contentView?.layoutSubtreeIfNeeded()
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
                if event.type == .leftMouseDown, event.clickCount == 2,
                   event.window === self.panel, let panel = self.panel {
                    // Only the mascot's top region handles double click. Text
                    // selection and the composer retain native mouse behavior.
                    let top = panel.frame.height - event.locationInWindow.y
                    let mascotSize = 36 + 36 * self.presentation.expansionProgress
                    if top <= 8 + mascotSize {
                        self.activateVoice()
                        return true
                    }
                }
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
        expansionAnimator.cancel()
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
        presentation.expansionProgress = 0
    }

    func close() {
        removePanel()
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        screenObserver = nil
        if let accessibilityObserver { NSWorkspace.shared.notificationCenter.removeObserver(accessibilityObserver) }
        accessibilityObserver = nil
        model = nil
        interactive = false
        reviewInteraction = nil
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
                menuBarHeight: menuBarHeight, preferredSize: CGSize(width: 340, height: 340)))
        }
        return nil
    }
}

private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
