import AppKit
import SwiftUI
import WebKit
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
private final class KeyboardNavigationBlocker: NSObject, WKNavigationDelegate {
    var attemptedNavigations = 0

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        attemptedNavigations += 1
        decisionHandler(.cancel)
    }
}

@MainActor
final class BrowserKeyboardTests: XCTestCase {
    private func window<V: View>(_ view: V, size: NSSize = NSSize(width: 500, height: 460)) -> NSWindow {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Jarvis · teclado con datos sintéticos"
        window.contentView = NSHostingView(rootView: view.environment(\.locale, Locale(identifier: "es")))
        window.setContentSize(size)
        window.makeKeyAndOrderFront(nil)
        return window
    }

    private func descendants(_ view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(descendants)
    }

    private func addressField(_ window: NSWindow) throws -> NSTextField {
        let fields = descendants(try XCTUnwrap(window.contentView)).compactMap { $0 as? NSTextField }
        return try XCTUnwrap(fields.first { $0.isEditable && $0.placeholderString == "Busca o escribe una dirección" })
    }

    private func sendKey(_ code: UInt16, characters: String, modifiers: NSEvent.ModifierFlags = [],
                         to window: NSWindow) throws {
        let event = try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero,
            modifierFlags: modifiers, timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, characters: characters,
            charactersIgnoringModifiers: code == 48 ? "\t" : characters, isARepeat: false, keyCode: code))
        // A standalone NSHostingView has no SwiftUI App main menu. Dispatch
        // key equivalents through its window before the regular responder path.
        if !modifiers.intersection([.command, .control]).isEmpty,
           window.performKeyEquivalent(with: event) { return }
        NSApplication.shared.sendEvent(event)
    }

    private func settle() async throws { try await Task.sleep(for: .milliseconds(200)) }

    func testBrowserTabAndShiftTabLeaveAndReturnToAddressWithoutNavigation() async throws {
        let store = JarvisBrowserStore(persistent: false)
        let blocker = KeyboardNavigationBlocker()
        store.webView.navigationDelegate = blocker
        // Adjacent editable controls make the key loop independent of the owner's macOS
        // preference for Tab navigation through buttons. Never change that preference.
        let browser = window(VStack {
            TextField("Campo anterior sintético", text: .constant(""))
            JarvisBrowserPanel(store: store, chat: JarvisChatStore(), close: {})
            TextField("Campo posterior sintético", text: .constant(""))
        })
        defer { browser.close() }
        try await settle()
        let address = try addressField(browser)
        XCTAssertTrue(browser.makeFirstResponder(address))
        let editor = try XCTUnwrap(address.currentEditor() as? NSTextView)
        editor.insertText("consulta sintética sin enviar", replacementRange: NSRange(location: 0, length: 0))
        editor.insertTab(nil)
        try await settle()
        XCTAssertNil(address.currentEditor(), "Tab debe salir del campo de dirección")
        let nextEditor = try XCTUnwrap(browser.firstResponder as? NSTextView)
        nextEditor.insertBacktab(nil)
        try await settle()
        XCTAssertNotNil(address.currentEditor(), "Mayús-Tab vuelve al campo sin atraparse en el navegador")
        XCTAssertEqual(address.stringValue, "consulta sintética sin enviar")
        XCTAssertEqual(blocker.attemptedNavigations, 0)
        XCTAssertNil(store.webView.url)
    }

    func testBrowserEscapeClosesPanelWithoutSendingDraftOrChangingConversation() async throws {
        let store = JarvisBrowserStore(persistent: false)
        let blocker = KeyboardNavigationBlocker()
        store.webView.navigationDelegate = blocker
        let chat = JarvisChatStore()
        let conversation = UUID()
        chat.conversationID = conversation
        chat.composerDraft = "Borrador sintético del chat"
        var closes = 0
        let browser = window(JarvisBrowserPanel(store: store, chat: chat, close: { closes += 1 }))
        defer { browser.close() }
        try await settle()
        let address = try addressField(browser)
        XCTAssertTrue(browser.makeFirstResponder(address))
        let editor = try XCTUnwrap(address.currentEditor() as? NSTextView)
        editor.insertText("consulta sintética sin enviar", replacementRange: NSRange(location: 0, length: 0))
        try sendKey(53, characters: "\u{1b}", to: browser)
        try await settle()
        XCTAssertEqual(closes, 1)
        XCTAssertEqual(chat.conversationID, conversation)
        XCTAssertEqual(chat.composerDraft, "Borrador sintético del chat")
        XCTAssertEqual(blocker.attemptedNavigations, 0)
        XCTAssertNil(store.webView.url)
    }

    func testBrowserTabShortcutsPreserveUnsubmittedAddressAndChatDraft() async throws {
        let tabs = JarvisBrowserTabs(persistent: false)
        let first = tabs.selected, chat = JarvisChatStore()
        chat.composerDraft = "Borrador sintético del chat"
        let browser = window(JarvisBrowserPanel(tabs: tabs, chat: chat, close: {}))
        defer { browser.close() }
        try await settle()
        let address = try addressField(browser)
        XCTAssertTrue(browser.makeFirstResponder(address))
        let editor = try XCTUnwrap(address.currentEditor() as? NSTextView)
        editor.insertText("dirección sin enviar", replacementRange: NSRange(location: 0, length: 0))
        try sendKey(17, characters: "t", modifiers: .command, to: browser)
        try await settle()
        XCTAssertEqual(tabs.pages.count, 2)
        let second = tabs.selectedID
        XCTAssertNotEqual(first.id, second)
        browser.makeFirstResponder(nil)
        try sendKey(37, characters: "l", modifiers: .command, to: browser)
        try await settle()
        XCTAssertNotNil(try addressField(browser).currentEditor(), "⌘L enfoca la dirección de la pestaña actual")
        try sendKey(48, characters: "\t", modifiers: [.control, .shift], to: browser)
        try await settle()
        XCTAssertEqual(tabs.selectedID, first.id)
        XCTAssertEqual(try addressField(browser).stringValue, "dirección sin enviar")
        try sendKey(48, characters: "\t", modifiers: .control, to: browser)
        try await settle()
        XCTAssertEqual(tabs.selectedID, second)
        XCTAssertEqual(chat.composerDraft, "Borrador sintético del chat")
        XCTAssertTrue(tabs.pages.allSatisfy { $0.webView.url == nil })
    }

    func testHUDEscapeClosesWithoutCancellingSyntheticJobOrApproval() async throws {
        let model = MenuBarModel()
        model.applyNotchPreview(.approval)
        let approval = try XCTUnwrap(model.pendingApproval)
        model.activeJobID = approval.jobID
        var closes = 0
        let hud = window(HUDView(model: model, close: { closes += 1 }, interactive: false),
                         size: NSSize(width: 360, height: 440))
        defer { hud.close() }
        try await settle()
        try sendKey(53, characters: "\u{1b}", to: hud)
        try await settle()
        XCTAssertEqual(closes, 1)
        XCTAssertEqual(model.pendingApproval, approval)
        XCTAssertEqual(model.activeJobID, approval.jobID)
        XCTAssertEqual(model.voiceState, .awaitingAuthorization)
    }
}
