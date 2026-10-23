import AppKit
import SwiftUI

/// Focus only the window explicitly requested by the user. Window levels stay
/// normal, so switching to another application works as usual.
@MainActor
enum RequestedWindowFocus {
    private final class Reference {
        weak var window: NSWindow?
        init(_ window: NSWindow) { self.window = window }
    }

    private static var windows: [String: Reference] = [:]
    private static var pending: Set<String> = []

    static func open(_ id: String, action: () -> Void) {
        pending.insert(id)
        NSApp.activate(ignoringOtherApps: true)
        action()
        // Let SwiftUI open the scene and MenuBarExtra finish dismissing first.
        DispatchQueue.main.async {
            guard let window = windows[id]?.window, window.isVisible || window.isMiniaturized else { return }
            fulfill(id, window: window)
        }
    }

    /// The window is open on this Space and at least partly in view.
    static func isShowing(_ id: String) -> Bool {
        guard let window = windows[id]?.window else { return false }
        return window.isVisible && !window.isMiniaturized && window.occlusionState.contains(.visible)
    }

    static func close(_ id: String, action: () -> Void) {
        // A decision can finish before SwiftUI has registered its window. Do not
        // let the deferred focus request bring that now-empty scene back.
        pending.remove(id)
        windows[id]?.window?.close()
        action()
    }

    fileprivate static func register(_ window: NSWindow, id: String) {
        windows[id] = Reference(window)
        JarvisDockController.shared.register(window, id: id)
        DispatchQueue.main.async { [weak window] in
            guard let window else { return }
            fulfill(id, window: window)
        }
    }

    private static func fulfill(_ id: String, window: NSWindow) {
        guard pending.remove(id) != nil else { return }
        if window.isMiniaturized { window.deminiaturize(nil) }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
}

/// Gives the focus action the actual scene window, without title matching or
/// changing its level. Attaching the view alone never steals focus.
struct RequestedWindowRegistration: NSViewRepresentable {
    let id: String

    func makeNSView(context: Context) -> WindowView {
        WindowView(id: id)
    }

    func updateNSView(_ nsView: WindowView, context: Context) {}

    final class WindowView: NSView {
        let id: String
        init(id: String) {
            self.id = id
            super.init(frame: .zero)
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window { RequestedWindowFocus.register(window, id: id) }
        }
    }
}
