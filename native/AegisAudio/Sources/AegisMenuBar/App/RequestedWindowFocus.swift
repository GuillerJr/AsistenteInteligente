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
            guard let window = windows[id]?.window, window.isVisible else { return }
            fulfill(id, window: window)
        }
    }

    fileprivate static func register(_ window: NSWindow, id: String) {
        windows[id] = Reference(window)
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
    var minimumContentSize: CGSize? = nil

    func makeNSView(context: Context) -> WindowView {
        WindowView(id: id, minimumContentSize: minimumContentSize)
    }

    func updateNSView(_ nsView: WindowView, context: Context) {
        nsView.minimumContentSize = minimumContentSize
        nsView.configureSizing()
    }

    final class WindowView: NSView {
        let id: String
        var minimumContentSize: CGSize?
        init(id: String, minimumContentSize: CGSize?) {
            self.id = id
            self.minimumContentSize = minimumContentSize
            super.init(frame: .zero)
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window {
                configureSizing()
                RequestedWindowFocus.register(window, id: id)
            }
        }

        func configureSizing() {
            guard let minimumContentSize, let window else { return }
            // Settings keeps a fixed AppKit style even with contentMinSize.
            // SwiftUI still owns layout; this only enables native edge resizing.
            DispatchQueue.main.async { [weak window] in
                guard let window else { return }
                if !window.styleMask.contains(.resizable) { window.styleMask.insert(.resizable) }
                if window.contentMinSize != minimumContentSize { window.contentMinSize = minimumContentSize }
                let unlimited = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
                if window.contentMaxSize != unlimited { window.contentMaxSize = unlimited }
            }
        }
    }
}
