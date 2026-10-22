import AppKit
import SwiftUI
import WebKit
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
private final class NavigationRecorder: NSObject, WKNavigationDelegate {
    var urls: [URL] = []
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url { urls.append(url) }
        decisionHandler(.cancel) // Never reach the network in a UI test.
    }
}

@MainActor
final class JarvisVisualAccessibilityTests: XCTestCase {
    private func window<V: View>(_ view: V, size: NSSize, dark: Bool = false) -> NSWindow {
        _ = NSApplication.shared
        // Ask this test process to materialize SwiftUI's virtual AX tree. These
        // application attributes do not change system accessibility preferences
        // or grant this runner access to any other application's UI.
        NSApp.accessibilitySetValue(true, forAttribute: .init(rawValue: "AXEnhancedUserInterface"))
        NSApp.accessibilitySetValue(true, forAttribute: .init(rawValue: "AXManualAccessibility"))
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Jarvis · datos sintéticos de prueba"
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.contentView = NSHostingView(rootView: view
            .environment(\.locale, Locale(identifier: "es"))
            .environment(\.colorScheme, dark ? .dark : .light))
        window.setContentSize(size)
        window.orderFront(nil)
        return window
    }

    private func settle() async throws { try await Task.sleep(for: .milliseconds(200)) }

    private func capture(_ window: NSWindow, name: String) throws {
        let view = try XCTUnwrap(window.contentView)
        view.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(view.bounds.width, 100)
        guard let root = ProcessInfo.processInfo.environment["AEGIS_UI_EVIDENCE_ROOT"] else { return }
        guard root.hasPrefix("/private/tmp/jarvis-") else { throw CocoaError(.fileWriteInvalidFileName) }
        let directory = URL(fileURLWithPath: root, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let bitmap = try XCTUnwrap(view.bitmapImageRepForCachingDisplay(in: view.bounds))
        view.cacheDisplay(in: view.bounds, to: bitmap)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: directory.appendingPathComponent(name + ".png"))
    }

    // SwiftUI's virtual AX elements expose the Objective-C selectors without
    // declaring conformance to NSAccessibilityProtocol. Walk that public API.
    private func accessibilityValue(_ object: NSObject, _ property: String) -> Any? {
        let selector = NSSelectorFromString(property)
        guard object.responds(to: selector) else { return nil }
        return object.perform(selector)?.takeUnretainedValue()
    }

    private func accessibilityElements(_ object: Any, depth: Int = 0) -> [NSObject] {
        guard depth < 30, let element = object as? NSObject else { return [] }
        return [element] + ((accessibilityValue(element, "accessibilityChildren") as? [Any]) ?? []).flatMap {
            accessibilityElements($0, depth: depth + 1)
        }
    }

    private func assertNamedButtons(_ window: NSWindow, surface: String) throws {
        let elements = accessibilityElements(try XCTUnwrap(window.contentView))
        let roles = [NSAccessibility.Role.button.rawValue, NSAccessibility.Role.popUpButton.rawValue,
                     NSAccessibility.Role.menuButton.rawValue]
        let buttons = elements.filter {
            guard roles.contains(accessibilityValue($0, "accessibilityRole") as? String ?? "") else { return false }
            // Native scrollbar increment/decrement parts are named through the
            // scrollbar relationship by AppKit; they are not app icon buttons.
            if let parent = accessibilityValue($0, "accessibilityParent") as? NSObject,
               accessibilityValue(parent, "accessibilityRole") as? String == NSAccessibility.Role.scrollBar.rawValue {
                return false
            }
            return true
        }
        XCTAssertFalse(buttons.isEmpty, "La superficie expone acciones al árbol de accesibilidad")
        for button in buttons {
            let names = ["accessibilityLabel", "accessibilityTitle", "accessibilityValueDescription",
                         "accessibilityDescription"].compactMap { accessibilityValue(button, $0) as? String }
            let named = names.contains { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            XCTAssertTrue(named, "Botón sin nombre en \(surface): \(button); atributos=\(names)")
        }
    }

    func testLightDarkSmallWindowsWithSyntheticContent() async throws {
        for dark in [false, true] {
            let model = MenuBarModel()
            model.applyNotchPreview(.idle)
            model.chat.transcript.restore([
                .init(role: .user, text: "¿Cómo organizo las tareas del proyecto esta semana?"),
                .init(role: .assistant, text: "Podemos ordenar las tareas por prioridad y reservar un momento para revisar los avances."),
            ])
            let settings = JarvisSettingsStore()
            settings.personality = JarvisPersonality(preset: "default", custom: "", presets: [
                .init(id: "default", label: "Predeterminada"), .init(id: "direct", label: "Directa"),
            ])
            let suffix = dark ? "dark" : "light"
            let chat = window(JarvisChatView(model: model, interactive: false, previewBrowser: true),
                              size: NSSize(width: 800, height: 640), dark: dark)
            try await settle()
            XCTAssertEqual(chat.contentView?.bounds.width, 800)
            try capture(chat, name: "chat-browser-800-" + suffix)
            try assertNamedButtons(chat, surface: "chat")
            chat.close()

            let preferences = window(JarvisSettingsView(model: model, store: settings, restart: {}, interactive: false),
                                     size: JarvisInterfaceStyle.settingsMinimumSize, dark: dark)
            try await settle()
            try capture(preferences, name: "settings-minimum-" + suffix)
            try assertNamedButtons(preferences, surface: "settings")
            preferences.close()

            let menu = window(MenuBarView(model: model, interactive: false, maximumContentHeight: 440),
                              size: NSSize(width: 340, height: 550), dark: dark)
            try await settle()
            try capture(menu, name: "popover-" + suffix)
            try assertNamedButtons(menu, surface: "popover")
            menu.close()

            model.applyNotchPreview(.approval)
            let approval = window(ApprovalView(model: model, interactive: false),
                                  size: NSSize(width: 560, height: 650), dark: dark)
            try await settle()
            try capture(approval, name: "approval-" + suffix)
            try assertNamedButtons(approval, surface: "approval")
            approval.close()
        }
    }

    func testBrowserReturnNavigatesAddressAndFreeTextWithNoNetwork() async throws {
        let store = JarvisBrowserStore(persistent: false)
        let recorder = NavigationRecorder()
        store.webView.navigationDelegate = recorder
        let browser = window(JarvisBrowserPanel(store: store, chat: JarvisChatStore(), close: {}),
                             size: NSSize(width: 500, height: 420))
        defer { browser.close() }
        try await settle()
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        let input = try XCTUnwrap(descendants(try XCTUnwrap(browser.contentView)).compactMap { $0 as? NSTextField }
            .first(where: { $0.isEditable }))
        for query in ["example.com/manual", "cómo organizar mi semana"] {
            browser.makeFirstResponder(input)
            let editor = try XCTUnwrap(input.currentEditor() as? NSTextView)
            editor.selectAll(nil)
            editor.insertText(query)
            editor.insertNewline(nil)
            for _ in 0..<30 {
                if recorder.urls.last == JarvisBrowserStore.destination(for: query) { break }
                try await Task.sleep(for: .milliseconds(50))
            }
            XCTAssertEqual(recorder.urls.last, JarvisBrowserStore.destination(for: query))
        }
    }
}
