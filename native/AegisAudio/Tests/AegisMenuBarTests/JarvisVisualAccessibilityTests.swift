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
            func describe(_ element: NSObject) -> String {
                ["accessibilityRole", "accessibilityIdentifier", "accessibilityHelp",
                 "accessibilityLabel", "accessibilityTitle", "accessibilityDescription"].map {
                    "\($0)=\(accessibilityValue(element, $0) as? String ?? "∅")"
                }.joined(separator: ", ")
            }
            let parent = (accessibilityValue(button, "accessibilityParent") as? NSObject).map(describe) ?? "∅"
            let children = (accessibilityValue(button, "accessibilityChildren") as? [NSObject] ?? []).map(describe)
            XCTAssertTrue(named, "Botón sin nombre en \(surface): \(button); atributos=\(names); "
                          + "nodo=[\(describe(button))]; padre=[\(parent)]; hijos=\(children)")
        }
    }

    private func press(_ identifier: String, in window: NSWindow) throws {
        let element = try XCTUnwrap(accessibilityElements(try XCTUnwrap(window.contentView)).first {
            accessibilityValue($0, "accessibilityIdentifier") as? String == identifier
        })
        let selector = NSSelectorFromString("accessibilityPerformPress")
        XCTAssertTrue(element.responds(to: selector))
        typealias Press = @convention(c) (NSObject, Selector) -> Bool
        let action = unsafeBitCast(element.method(for: selector), to: Press.self)
        XCTAssertTrue(action(element, selector))
    }

    func testWorkspaceWelcomeAndContextAtSmallAndWideSizes() async throws {
        for dark in [false, true] {
            for width in [800, 1280] {
                let model = MenuBarModel()
                model.applyNotchPreview(.idle)
                model.chat.showHistory = true
                model.chat.projects = [.init(project_id: UUID().uuidString.lowercased(),
                    name: "Proyecto de demostración", color: "clay", icon: "folder",
                    instructions: "Organiza las tareas por prioridad y resume los resultados.",
                    files: [.init(file_id: "demo", name: "Plan de ejemplo.md", size: 120)], chats: 0)]
                model.chat.newChatProjectID = model.chat.projects[0].id
                model.chat.conversations = [.init(id: UUID(), title: "Una conversación de ejemplo",
                    updatedAt: ISO8601DateFormatter().string(from: Date()), archived: false)]
                let workspace = window(JarvisChatView(model: model, interactive: false),
                    size: NSSize(width: width, height: 720), dark: dark)
                defer { workspace.close() }
                try await settle()
                let suffix = "\(width)-" + (dark ? "dark" : "light")
                XCTAssertEqual(workspace.contentView?.bounds.width, CGFloat(width))
                try assertNamedButtons(workspace, surface: "workspace-welcome")
                try capture(workspace, name: "workspace-welcome-" + suffix)

                try press("chat-context-toggle", in: workspace)
                try await settle()
                let elements = accessibilityElements(try XCTUnwrap(workspace.contentView))
                XCTAssertTrue(elements.contains {
                    accessibilityValue($0, "accessibilityIdentifier") as? String == "chat-context-panel"
                })
                XCTAssertEqual(workspace.contentView?.bounds.width, CGFloat(width))
                try assertNamedButtons(workspace, surface: "workspace-context")
                try capture(workspace, name: "workspace-context-" + suffix)

                // A starter prepares text only; the synthetic view cannot submit IPC.
                try press("chat-starter-plan", in: workspace)
                try await settle()
                XCTAssertEqual(model.chat.composerDraft, JarvisChatStarter.plan.prompt)
                XCTAssertTrue(model.chat.planMode)
                XCTAssertTrue(model.chat.transcript.messages.isEmpty)
                XCTAssertNil(model.chat.transcript.jobID)

                // A second right panel replaces context and preserves the draft.
                try press("chat-browser-toggle", in: workspace)
                try await settle()
                XCTAssertFalse(accessibilityElements(try XCTUnwrap(workspace.contentView)).contains {
                    accessibilityValue($0, "accessibilityIdentifier") as? String == "chat-context-panel"
                })
                XCTAssertEqual(model.chat.composerDraft, JarvisChatStarter.plan.prompt)
            }
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
            let approval = window(JarvisDecisionCard(model: model).frame(width: 520).padding(20),
                                  size: NSSize(width: 560, height: 420), dark: dark)
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

    func testSearchTabsAndActionHistoryInSmallLightAndDarkWindows() async throws {
        for dark in [false, true] {
            let suffix = dark ? "dark" : "light"
            let model = MenuBarModel()
            model.applyNotchPreview(.idle)
            model.chat.historyQuery = "revisión"
            model.chat.conversations = [
                .init(id: UUID(), title: "Plan del proyecto de prueba", updatedAt: "2026-10-22T12:00:00Z",
                      archived: false, searchMatch: .init(turnID: UUID(), sequence: 3,
                        excerpt: "La revisión de las tareas del viernes quedó preparada para el equipo.")),
                .init(id: UUID(), title: "Notas de una conversación antigua", updatedAt: "2026-09-01T12:00:00Z",
                      archived: false, searchMatch: .init(turnID: UUID(), sequence: 48,
                        excerpt: "Guardamos la revisión del documento antes de continuar con las pruebas.")),
            ]
            let search = window(JarvisChatHistoryView(model: model, interactive: false),
                                size: NSSize(width: 320, height: 620), dark: dark)
            try await settle()
            try capture(search, name: "message-search-" + suffix)
            try assertNamedButtons(search, surface: "búsqueda de mensajes")
            search.close()

            let tabs = JarvisBrowserTabs(persistent: false)
            tabs.selected.title = "Manual de prueba"
            let second = try XCTUnwrap(tabs.newTab())
            second.title = "Consulta con un título de página largo para revisar el espacio"
            let third = try XCTUnwrap(tabs.newTab())
            third.title = "Notas"
            tabs.select(second.id)
            let browser = window(JarvisBrowserPanel(tabs: tabs, chat: model.chat, close: {}),
                                 size: NSSize(width: 400, height: 480), dark: dark)
            try await settle()
            try capture(browser, name: "browser-tabs-small-" + suffix)
            try assertNamedButtons(browser, surface: "pestañas del navegador")
            browser.close()
            tabs.pages.forEach { $0.dispose() }

            let conversation = UUID()
            let entries = ["reported_success", "failed", "unconfirmed", "awaiting_approval"].enumerated().map {
                JarvisActionEntry(id: UUID(), sequence: 4 - $0.offset,
                                  timestamp: Date(timeIntervalSince1970: 1_792_670_400),
                                  toolName: $0.offset == 0 ? "computer_use" : "browser_search",
                                  state: $0.element, conversationID: conversation)
            }
            let history = window(JarvisActionHistoryView(model: model, interactive: false,
                                store: JarvisActionHistoryStore(previewEntries: entries)),
                                 size: NSSize(width: 620, height: 560), dark: dark)
            try await settle()
            try capture(history, name: "action-history-small-" + suffix)
            try assertNamedButtons(history, surface: "historial de acciones")
            history.close()
        }
    }
}
