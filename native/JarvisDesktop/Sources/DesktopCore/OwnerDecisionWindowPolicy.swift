import AppKit

/// Routing only. It neither inspects window contents nor owns any window.
/// The caller passes its own app's transient, front-to-back window inventory.
@MainActor public enum OwnerDecisionWindowPolicy {
    public static let mainIdentifier = "jarvis.main"
    public static let settingsIdentifier = "jarvis.settings"

    public static func awaitingOwner(_ phases: [String?]) -> Bool {
        phases.contains { $0 == "awaiting_owner" }
    }

    public static func target(windows: [NSWindow], modal: NSWindow?, key: NSWindow?,
                              awaitingOwner: Bool, preserveVisibleOnReopen: Bool) -> NSWindow? {
        func visible(_ window: NSWindow) -> Bool { window.isVisible && !window.isMiniaturized }
        func owned(_ window: NSWindow) -> Bool {
            windows.contains { $0 === window || $0.attachedSheet === window }
        }
        // Never replace this app's live modal/sheet with its main browser.
        if let modal, owned(modal), visible(modal) { return modal }
        if let key, owned(key), visible(key), let sheet = key.attachedSheet, visible(sheet) { return sheet }
        if let key, visible(key), windows.contains(where: { $0.attachedSheet === key }) { return key }
        for parent in windows where visible(parent) {
            if let sheet = parent.attachedSheet, visible(sheet) { return sheet }
        }
        if awaitingOwner, let settings = windows.first(where: {
            $0.identifier?.rawValue == settingsIdentifier && visible($0)
        }) { return settings }
        // A Dock reopen with visible windows should preserve its existing
        // context, even when it is not an approval. The explicit main-window
        // command keeps its usual behavior once no owner decision is visible.
        if preserveVisibleOnReopen {
            if let key, owned(key), visible(key), key.canBecomeKey { return key }
            if let current = windows.first(where: { visible($0) && $0.canBecomeKey }) { return current }
        }
        return windows.first { $0.identifier?.rawValue == mainIdentifier }
    }
}
