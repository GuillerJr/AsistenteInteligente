import Foundation
import WebKit
import XCTest
@testable import AegisMenuBar

@MainActor
private final class TabNavigationBlocker: NSObject, WKNavigationDelegate {
    var requestedURLs: [URL] = []

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url { requestedURLs.append(url) }
        decisionHandler(.cancel)
    }
}

@MainActor
private final class TabPausedReader {
    private var continuation: CheckedContinuation<String?, any Error>?
    private var arrival: CheckedContinuation<Void, Never>?

    func read() async throws -> String? {
        try await withCheckedThrowingContinuation {
            continuation = $0
            arrival?.resume()
            arrival = nil
        }
    }

    func waitUntilStarted() async {
        if continuation != nil { return }
        await withCheckedContinuation { arrival = $0 }
    }

    func finish(_ result: Result<String?, any Error> = .success("Texto sintético")) {
        continuation?.resume(with: result)
        continuation = nil
    }
}

@MainActor
private final class TabVisibility {
    var isVisible = true
}

@MainActor
final class BrowserTabsTests: XCTestCase {
    private func isolatedTabs() -> (JarvisBrowserTabs, TabNavigationBlocker) {
        let blocker = TabNavigationBlocker()
        let make = {
            let page = JarvisBrowserStore(persistent: false)
            page.webView.navigationDelegate = blocker
            return page
        }
        return (JarvisBrowserTabs(initialPage: make(), makePage: make), blocker)
    }

    private func pendingRead(_ page: JarvisBrowserStore, chat: JarvisChatStore,
                             reader: TabPausedReader) throws -> Task<Void, Never> {
        let identity = JarvisBrowserPageCapture.Page(revision: page.documentRevision,
            url: try XCTUnwrap(URL(string: "https://example.invalid/sintetico")), title: "Página sintética")
        return try XCTUnwrap(page.pageCapture.start(page: identity, to: chat,
            isCurrentPage: { _ in true }, readText: reader.read))
    }

    func testTabLimitPreservesSelectionAndExistingDrafts() throws {
        let (tabs, _) = isolatedTabs()
        let original = tabs.selected
        original.addressDraft = "dirección sintética sin enviar"
        for _ in 1..<JarvisBrowserTabs.maximumTabs { XCTAssertNotNil(tabs.newTab()) }
        let before = tabs.pages.map(\.id), selected = tabs.selectedID
        XCTAssertFalse(tabs.canAddTab)
        XCTAssertNil(tabs.newTab())
        XCTAssertEqual(tabs.pages.map(\.id), before)
        XCTAssertEqual(tabs.selectedID, selected)
        XCTAssertEqual(original.addressDraft, "dirección sintética sin enviar")
        XCTAssertNotNil(tabs.notice)
        tabs.close(original.id)
        XCTAssertTrue(tabs.canAddTab)
        XCTAssertNil(tabs.notice)
        XCTAssertNotNil(tabs.newTab())
        XCTAssertEqual(tabs.pages.count, JarvisBrowserTabs.maximumTabs)
    }

    func testSelectedPagesKeepIndependentDraftsMetadataAndWebViews() throws {
        let (tabs, _) = isolatedTabs()
        let first = tabs.selected
        first.address = "https://example.invalid/primera"
        first.title = "Primera"
        first.addressDraft = "borrador de primera"
        let second = try XCTUnwrap(tabs.newTab())
        second.address = "https://example.invalid/segunda"
        second.title = "Segunda"
        second.addressDraft = "borrador de segunda"
        XCTAssertFalse(first.webView === second.webView)
        XCTAssertFalse(first.webView.configuration.websiteDataStore.isPersistent)
        XCTAssertFalse(second.webView.configuration.websiteDataStore.isPersistent)
        tabs.select(first.id)
        XCTAssertEqual(tabs.selected.title, "Primera")
        XCTAssertEqual(tabs.selected.addressDraft, "borrador de primera")
        tabs.selectAdjacent(1)
        XCTAssertEqual(tabs.selectedID, second.id)
        XCTAssertEqual(tabs.selected.addressDraft, "borrador de segunda")
        tabs.selectAdjacent(1)
        XCTAssertEqual(tabs.selectedID, first.id)
        tabs.selectAdjacent(-1)
        XCTAssertEqual(tabs.selectedID, second.id)
        tabs.select(UUID())
        XCTAssertEqual(tabs.selectedID, second.id)
    }

    func testCloseSelectsNearestPageAndReplacesLastWithAnEmptyPage() throws {
        let (tabs, _) = isolatedTabs()
        let first = tabs.selected
        let second = try XCTUnwrap(tabs.newTab())
        let third = try XCTUnwrap(tabs.newTab())
        tabs.select(second.id)
        tabs.close(first.id)
        XCTAssertEqual(tabs.selectedID, second.id)
        tabs.close(second.id)
        XCTAssertEqual(tabs.selectedID, third.id)
        XCTAssertTrue(second.isClosed)
        XCTAssertNil(second.webView.navigationDelegate)
        XCTAssertNil(second.openNewTab)
        tabs.close(third.id)
        XCTAssertEqual(tabs.pages.count, 1)
        XCTAssertNotEqual(tabs.selectedID, third.id)
        XCTAssertEqual(tabs.selected.addressDraft, "")
        XCTAssertNil(tabs.selected.webView.url)
        let replacement = tabs.selectedID
        tabs.close(third.id)
        XCTAssertEqual(tabs.selectedID, replacement)
    }

    func testChatLinksReuseEmptyOrMatchingTabAndNeverReplaceAnOccupiedTabAtLimit() throws {
        let (tabs, _) = isolatedTabs()
        let first = tabs.selected
        let url = try XCTUnwrap(URL(string: "https://example.invalid/primera"))
        XCTAssertTrue(tabs.openFromChat(url))
        XCTAssertEqual(tabs.pages.count, 1)
        XCTAssertEqual(tabs.selectedID, first.id)
        let secondURL = try XCTUnwrap(URL(string: "https://example.invalid/segunda"))
        XCTAssertTrue(tabs.openFromChat(secondURL))
        XCTAssertEqual(tabs.pages.count, 2)
        XCTAssertNotEqual(tabs.selectedID, first.id)
        XCTAssertTrue(tabs.openFromChat(url))
        XCTAssertEqual(tabs.pages.count, 2)
        XCTAssertEqual(tabs.selectedID, first.id)
        while tabs.canAddTab {
            let page = try XCTUnwrap(tabs.newTab())
            page.addressDraft = "borrador que no debe perderse"
        }
        let before = tabs.pages.map(\.addressDraft), selected = tabs.selectedID
        XCTAssertFalse(tabs.openFromChat(URL(string: "https://example.invalid/tercera")!))
        XCTAssertEqual(tabs.pages.map(\.addressDraft), before)
        XCTAssertEqual(tabs.selectedID, selected)
        XCTAssertNotNil(tabs.notice)
        XCTAssertFalse(tabs.openFromChat(URL(fileURLWithPath: "/tmp/sintetico")))
    }

    func testNewWindowRequiresExplicitWebLinkInSelectedVisiblePage() throws {
        let (tabs, _) = isolatedTabs()
        let first = tabs.selected
        let url = try XCTUnwrap(URL(string: "https://example.invalid/enlace"))
        first.reviewNewTabNavigation(url, activatedLink: false)
        XCTAssertEqual(tabs.pages.count, 1)
        XCTAssertNotNil(first.failure)
        first.reviewNewTabNavigation(URL(fileURLWithPath: "/tmp/sintetico"), activatedLink: true)
        XCTAssertEqual(tabs.pages.count, 1)
        first.reviewNewTabNavigation(url, activatedLink: true)
        XCTAssertEqual(tabs.pages.count, 2)
        XCTAssertNotEqual(tabs.selectedID, first.id)
        first.reviewNewTabNavigation(url, activatedLink: true)
        XCTAssertEqual(tabs.pages.count, 2, "Una pestaña de fondo no crea ventanas")
        tabs.suspend()
        tabs.selected.reviewNewTabNavigation(url, activatedLink: true)
        XCTAssertEqual(tabs.pages.count, 2, "El navegador oculto no crea ventanas")
        tabs.resume()
        tabs.selected.reviewNewTabNavigation(url, activatedLink: true)
        XCTAssertEqual(tabs.pages.count, 3)
    }

    func testLateClosedPageCallbacksCannotResurrectTabsOrExternalApproval() throws {
        let (tabs, _) = isolatedTabs()
        let closing = tabs.selected
        let callback = try XCTUnwrap(closing.openNewTab)
        tabs.close(closing.id)
        let replacement = tabs.selectedID
        XCTAssertFalse(callback(URL(string: "https://example.invalid/tardia")!))
        closing.webView(closing.webView, didStartProvisionalNavigation: nil)
        closing.reviewExternalNavigation(URL(string: "shortcuts://synthetic")!, activatedLink: true)
        closing.open("https://example.invalid/cerrada")
        XCTAssertFalse(closing.loading)
        XCTAssertNil(closing.externalURL)
        XCTAssertEqual(tabs.pages.count, 1)
        XCTAssertEqual(tabs.selectedID, replacement)
    }

    func testSwitchAwayAndBackDiscardsPendingTextWithoutChangingChat() async throws {
        let (tabs, _) = isolatedTabs()
        let original = tabs.selected, chat = JarvisChatStore(), reader = TabPausedReader()
        chat.composerDraft = "Borrador sintético"
        let task = try pendingRead(original, chat: chat, reader: reader)
        await reader.waitUntilStarted()
        _ = try XCTUnwrap(tabs.newTab())
        tabs.select(original.id)
        XCTAssertFalse(original.pageCapture.isReading)
        reader.finish()
        await task.value
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertNil(chat.error)
        XCTAssertEqual(chat.composerDraft, "Borrador sintético")
    }

    func testCloseDiscardsPendingReadAndReleasesClosedPage() async throws {
        let (tabs, _) = isolatedTabs()
        var closing: JarvisBrowserStore? = tabs.selected
        weak var weakPage = closing
        let id = tabs.selectedID, chat = JarvisChatStore(), reader = TabPausedReader()
        let task = try pendingRead(try XCTUnwrap(closing), chat: chat, reader: reader)
        await reader.waitUntilStarted()
        tabs.close(id)
        closing = nil
        XCTAssertNil(weakPage, "Una lectura cancelada no retiene la sesión de pestaña cerrada")
        reader.finish()
        await task.value
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertNil(chat.error)
    }

    func testHidePanelCancelsEveryPendingReadAndDropsLateErrorAfterReopen() async throws {
        enum SyntheticError: Error { case late }
        let (tabs, _) = isolatedTabs()
        let first = tabs.selected, second = try XCTUnwrap(tabs.newTab())
        let chat = JarvisChatStore(), firstReader = TabPausedReader(), secondReader = TabPausedReader()
        let firstTask = try pendingRead(first, chat: chat, reader: firstReader)
        let secondTask = try pendingRead(second, chat: chat, reader: secondReader)
        await firstReader.waitUntilStarted(); await secondReader.waitUntilStarted()
        first.externalURL = URL(string: "shortcuts://synthetic")
        tabs.suspend()
        XCTAssertFalse(first.pageCapture.isReading)
        XCTAssertFalse(second.pageCapture.isReading)
        XCTAssertNil(first.externalURL)
        XCTAssertEqual(tabs.pages.count, 2)
        XCTAssertEqual(tabs.selectedID, second.id)
        tabs.resume()
        firstReader.finish(.failure(SyntheticError.late)); secondReader.finish()
        await firstTask.value; await secondTask.value
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertNil(chat.error)
    }

    func testWebKitHistoryIsIndependentAcrossTabsUsingOnlyDataPages() async throws {
        let tabs = JarvisBrowserTabs(persistent: false)
        let first = tabs.selected
        try await loadDataPage("Primera A", page: first)
        try await loadDataPage("Primera B", page: first)
        XCTAssertTrue(first.canGoBack)
        let second = try XCTUnwrap(tabs.newTab())
        try await loadDataPage("Segunda", page: second)
        _ = try await second.webView.evaluateJavaScript("document.title = 'Segunda actualizada'")
        try await waitForTitle("Segunda actualizada", page: second)
        XCTAssertFalse(second.canGoBack)
        tabs.select(first.id)
        first.goBack()
        try await waitForTitle("Primera A", page: first)
        XCTAssertEqual(second.title, "Segunda actualizada")
        XCTAssertTrue(first.canGoForward)
        first.goForward()
        try await waitForTitle("Primera B", page: first)
        XCTAssertEqual(second.title, "Segunda actualizada")
        first.reload()
        try await waitForTitle("Primera B", page: first)
        tabs.close(first.id); tabs.close(second.id)
    }

    func testHidingBeforeTheReadTaskStartsCannotAttachThePage() async throws {
        let tabs = JarvisBrowserTabs(persistent: false), chat = JarvisChatStore()
        let page = tabs.selected
        try await loadDataPage("Página de prueba", page: page)
        let visibility = TabVisibility()
        tabs.askAboutSelectedPage(in: chat, isVisible: { visibility.isVisible })
        XCTAssertTrue(page.pageCapture.isReading)
        // SwiftUI's onDisappear may arrive later than its owner's visibility state.
        visibility.isVisible = false
        for _ in 0..<100 where page.pageCapture.isReading { await Task.yield() }
        XCTAssertFalse(page.pageCapture.isReading)
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertNil(chat.error)
        tabs.close(page.id)
    }

    private func loadDataPage(_ title: String, page: JarvisBrowserStore) async throws {
        let html = "<html><head><title>\(title)</title></head><body>Texto sintético</body></html>"
        let encoded = Data(html.utf8).base64EncodedString()
        page.webView.load(URLRequest(url: try XCTUnwrap(URL(string: "data:text/html;charset=utf-8;base64,\(encoded)"))))
        try await waitForTitle(title, page: page)
    }

    private func waitForTitle(_ title: String, page: JarvisBrowserStore) async throws {
        for _ in 0..<200 {
            if page.title == title && !page.loading { return }
            try await Task.sleep(for: .milliseconds(25))
        }
        XCTFail("La página sintética no terminó de cargar: \(title); título observable: \(page.title), título WebKit: \(page.webView.title ?? "nil"), cargando: \(page.loading), cargando WebKit: \(page.webView.isLoading)")
    }
}
