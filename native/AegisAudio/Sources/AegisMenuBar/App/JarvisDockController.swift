import AppKit
import OSLog

/// Only persistent, user-opened scenes participate in the Dock lifecycle.
/// Closing a window is distinct from minimizing it or hiding the application.
@MainActor
final class JarvisDockController: NSObject {
    static let shared = JarvisDockController()
    private static let sceneIDs = ["chat", "settings", "wake-word-enrollment", "speaker-enrollment"]
    private let logger = Logger(subsystem: "com.aegis.menubar", category: "Dock")

    private final class Reference {
        weak var window: NSWindow?
        var isOpen = false
        var isClosing = false
        var lastFocus = 0
        init(_ window: NSWindow) { self.window = window }
    }

    private var windows: [String: Reference] = [:]
    private var focusSequence = 0

    func register(_ window: NSWindow, id: String) {
        guard Self.sceneIDs.contains(id) else { return }
        if windows[id]?.window === window { return }
        if let previous = windows[id]?.window {
            NotificationCenter.default.removeObserver(self, name: nil, object: previous)
        }
        windows[id] = Reference(window)
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(windowBecameKey(_:)),
                           name: NSWindow.didBecomeKeyNotification, object: window)
        center.addObserver(self, selector: #selector(windowUpdated(_:)),
                           name: NSWindow.didUpdateNotification, object: window)
        center.addObserver(self, selector: #selector(windowUpdated(_:)),
                           name: NSWindow.didMiniaturizeNotification, object: window)
        center.addObserver(self, selector: #selector(windowWillClose(_:)),
                           name: NSWindow.willCloseNotification, object: window)
        DispatchQueue.main.async { [weak self, weak window] in
            guard let self, let window else { return }
            self.observeVisibleWindow(window)
        }
    }

    func show(_ window: NSWindow) {
        guard let reference = reference(for: window) else { return }
        markOpen(reference)
        if window.isMiniaturized { window.deminiaturize(nil) }
        NSApp.unhide(nil)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    /// Dock activation restores the last principal window, including a minimized
    /// one, without opening a scene the user has already closed.
    @discardableResult
    func restoreLastWindow() -> Bool {
        guard let window = openReferences.max(by: { $0.lastFocus < $1.lastFocus })?.window else {
            return false
        }
        show(window)
        return true
    }

    func dockMenu() -> NSMenu? {
        let menu = NSMenu(title: "Jarvis")
        for id in Self.sceneIDs {
            guard let reference = windows[id], reference.isOpen, let window = reference.window else { continue }
            let item = NSMenuItem(title: window.title, action: #selector(selectWindow(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = id
            item.state = window.isKeyWindow ? .on : .off
            menu.addItem(item)
        }
        return menu.items.isEmpty ? nil : menu
    }

    private var openReferences: [Reference] {
        windows.values.filter { $0.isOpen && $0.window != nil }
    }

    private func reference(for window: NSWindow) -> Reference? {
        windows.values.first { $0.window === window }
    }

    private func markOpen(_ reference: Reference) {
        reference.isOpen = true
        reference.isClosing = false
        focusSequence += 1
        reference.lastFocus = focusSequence
        updateActivationPolicy()
    }

    private func observeVisibleWindow(_ window: NSWindow) {
        guard let reference = reference(for: window), !reference.isOpen, !reference.isClosing,
              window.isVisible || window.isMiniaturized else { return }
        markOpen(reference)
    }

    private func updateActivationPolicy() {
        let policy: NSApplication.ActivationPolicy = openReferences.isEmpty ? .accessory : .regular
        guard NSApp.activationPolicy() != policy else { return }
        if !NSApp.setActivationPolicy(policy) {
            logger.error("Could not update Dock activation policy")
        }
    }

    @objc private func windowBecameKey(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, let reference = reference(for: window) else { return }
        markOpen(reference)
    }

    @objc private func windowUpdated(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        observeVisibleWindow(window)
    }

    @objc private func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, let reference = reference(for: window) else { return }
        reference.isOpen = false
        reference.isClosing = true
        // Wait until AppKit finishes closing; another scene may open meanwhile.
        DispatchQueue.main.async { [weak self] in self?.updateActivationPolicy() }
    }

    @objc private func selectWindow(_ item: NSMenuItem) {
        guard let id = item.representedObject as? String, let reference = windows[id],
              reference.isOpen, let window = reference.window else { return }
        show(window)
    }
}
