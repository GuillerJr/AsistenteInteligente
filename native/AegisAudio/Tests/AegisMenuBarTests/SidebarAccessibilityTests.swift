import AppKit
import SwiftUI
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class SidebarAccessibilityTests: XCTestCase {
    private func window(_ model: MenuBarModel) -> NSWindow {
        _ = NSApplication.shared
        NSApp.accessibilitySetValue(true, forAttribute: .init(rawValue: "AXEnhancedUserInterface"))
        NSApp.accessibilitySetValue(true, forAttribute: .init(rawValue: "AXManualAccessibility"))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 300, height: 760),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Barra lateral · datos sintéticos"
        window.contentView = NSHostingView(rootView: JarvisChatHistoryView(model: model, interactive: false)
            .environment(\.locale, Locale(identifier: "es")))
        window.orderFront(nil)
        return window
    }

    private func value(_ object: NSObject, _ property: String) -> Any? {
        let selector = NSSelectorFromString(property)
        guard object.responds(to: selector) else { return nil }
        return object.perform(selector)?.takeUnretainedValue()
    }

    private func descendants(_ object: Any, depth: Int = 0) -> [NSObject] {
        guard depth < 30, let object = object as? NSObject else { return [] }
        return [object] + ((value(object, "accessibilityChildren") as? [Any]) ?? []).flatMap {
            descendants($0, depth: depth + 1)
        }
    }

    private func element(_ identifier: String, in window: NSWindow) -> NSObject? {
        guard let root = window.contentView else { return nil }
        return descendants(root).first { value($0, "accessibilityIdentifier") as? String == identifier }
    }

    private func press(_ object: NSObject) throws {
        let selector = NSSelectorFromString("accessibilityPerformPress")
        XCTAssertTrue(object.responds(to: selector))
        typealias Press = @convention(c) (NSObject, Selector) -> Bool
        let action = unsafeBitCast(object.method(for: selector), to: Press.self)
        XCTAssertTrue(action(object, selector))
    }

    private func settle() async throws { try await Task.sleep(for: .milliseconds(200)) }

    func testSearchActionAndKeyboardRequestRevealCollapsedHistory() async throws {
        let model = MenuBarModel()
        model.applyNotchPreview(.idle)
        let entry = ChatConversationSummary(id: UUID(), title: "Conversación sintética",
                                           updatedAt: ISO8601DateFormatter().string(from: Date()), archived: false)
        model.chat.conversations = [entry]
        let window = window(model)
        defer { window.close() }
        try await settle()
        let identifier = "sidebar-chat-\(entry.id.uuidString)"
        XCTAssertNotNil(element(identifier, in: window))
        try press(XCTUnwrap(element("sidebar-history-toggle", in: window)))
        try await settle()
        XCTAssertNil(element(identifier, in: window))

        try press(XCTUnwrap(element("sidebar-search", in: window)))
        try await settle()
        XCTAssertNotNil(element(identifier, in: window))
        XCTAssertNotNil(element("sidebar-search-field", in: window))

        try press(XCTUnwrap(element("sidebar-history-toggle", in: window)))
        try await settle()
        XCTAssertNil(element(identifier, in: window))
        model.chat.searchFocusPending = true
        model.chat.searchFocusRequest = UUID() // Same event as the existing Cmd+F command.
        try await settle()
        XCTAssertFalse(model.chat.searchFocusPending)
        XCTAssertNotNil(element(identifier, in: window))
        XCTAssertNotNil(element("sidebar-settings", in: window))
        XCTAssertNotNil(element("sidebar-action-history", in: window))
    }

    func testSearchAndTrashRowsRemainVisibleAfterHistoryWasCollapsed() async throws {
        let model = MenuBarModel()
        model.applyNotchPreview(.idle)
        let entry = ChatConversationSummary(id: UUID(), title: "Coincidencia sintética",
                                           updatedAt: "2026-10-01T12:00:00Z", archived: false)
        model.chat.conversations = [entry]
        let window = window(model)
        defer { window.close() }
        try await settle()
        try press(XCTUnwrap(element("sidebar-history-toggle", in: window)))
        try await settle()

        model.chat.historyQuery = "coincidencia"
        model.chat.conversations = [entry]
        try await settle()
        XCTAssertNotNil(element("sidebar-chat-\(entry.id.uuidString)", in: window))
        XCTAssertNil(element("sidebar-history-toggle", in: window))

        model.chat.historyQuery = ""
        model.chat.showTrash = true
        model.chat.conversations = [entry]
        try await settle()
        XCTAssertNil(element("sidebar-chat-\(entry.id.uuidString)", in: window))
        let restore = try XCTUnwrap(descendants(try XCTUnwrap(window.contentView)).first {
            value($0, "accessibilityLabel") as? String == "Restaurar \(entry.title)"
        })
        XCTAssertEqual(value(restore, "accessibilityRole") as? String, NSAccessibility.Role.button.rawValue)
        // Restoring is an independently reachable action, never nested in an open-chat button.
        if let parent = value(restore, "accessibilityParent") as? NSObject {
            XCTAssertNotEqual(value(parent, "accessibilityRole") as? String, NSAccessibility.Role.button.rawValue)
        }
    }
}
