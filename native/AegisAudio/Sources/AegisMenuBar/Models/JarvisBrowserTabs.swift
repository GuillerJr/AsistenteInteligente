import Foundation
import Observation

/// Window-owned browser sessions. Tab addresses and drafts stay in memory.
@MainActor @Observable
final class JarvisBrowserTabs {
    static let maximumTabs = 8
    private(set) var pages: [JarvisBrowserStore]
    private(set) var selectedID: UUID
    private(set) var suspended = false
    var notice: String?
    @ObservationIgnored private let makePage: () -> JarvisBrowserStore

    var selected: JarvisBrowserStore { pages.first { $0.id == selectedID } ?? pages[0] }
    var canAddTab: Bool { pages.count < Self.maximumTabs }

    init(persistent: Bool = true) {
        let makePage = { JarvisBrowserStore(persistent: persistent) }
        let initial = makePage()
        self.makePage = makePage
        pages = [initial]
        selectedID = initial.id
        connect(initial)
    }

    /// Fixtures can supply isolated pages whose navigation never reaches a network.
    init(initialPage: JarvisBrowserStore, makePage: @escaping () -> JarvisBrowserStore) {
        self.makePage = makePage
        pages = [initialPage]
        selectedID = initialPage.id
        connect(initialPage)
    }

    @discardableResult
    func newTab() -> JarvisBrowserStore? {
        guard canAddTab else {
            notice = String(localized: "Puedes abrir hasta 8 pestañas. Cierra una antes de abrir otra.", bundle: .module)
            return nil
        }
        let page = makePage()
        connect(page)
        pages.append(page)
        select(page.id)
        notice = nil
        return page
    }

    func select(_ id: UUID) {
        guard id != selectedID, pages.contains(where: { $0.id == id }) else { return }
        invalidateCaptures()
        selectedID = id
    }

    func selectAdjacent(_ offset: Int) {
        guard pages.count > 1, let index = pages.firstIndex(where: { $0.id == selectedID }) else { return }
        let destination = ((index + offset) % pages.count + pages.count) % pages.count
        select(pages[destination].id)
    }

    func close(_ id: UUID) {
        guard let index = pages.firstIndex(where: { $0.id == id }) else { return }
        let closing = pages[index]
        closing.dispose()
        pages.remove(at: index)
        if pages.isEmpty {
            let blank = makePage()
            connect(blank)
            pages = [blank]
            selectedID = blank.id
        } else if selectedID == id {
            invalidateCaptures()
            selectedID = pages[min(index, pages.count - 1)].id
        }
        notice = nil
    }

    /// Chat links reuse an already-open URL or an untouched empty tab. At capacity
    /// the owner gets a notice; an occupied page is never replaced implicitly.
    @discardableResult
    func openFromChat(_ url: URL) -> Bool {
        guard Self.isWebURL(url) else { return false }
        if let existing = pages.first(where: { $0.webView.url == url || $0.address == url.absoluteString }) {
            select(existing.id)
            return true
        }
        if let blank = pages.first(where: { $0.address.isEmpty && $0.addressDraft.isEmpty
            && $0.webView.url == nil && !$0.loading }) {
            select(blank.id)
            blank.open(url)
            return true
        }
        guard let page = newTab() else { return false }
        page.open(url)
        return true
    }

    func askAboutSelectedPage(in chat: JarvisChatStore,
                             isVisible: @escaping @MainActor () -> Bool = { true }) {
        guard !suspended, isVisible() else { return }
        let id = selectedID
        selected.askAboutPage(in: chat) { [weak self] in
            self?.suspended == false && self?.selectedID == id && isVisible()
        }
    }

    func invalidateCaptures() {
        for page in pages { page.deactivate() }
    }

    func suspend() {
        suspended = true
        invalidateCaptures()
    }

    func resume() { suspended = false }

    private func connect(_ page: JarvisBrowserStore) {
        page.openNewTab = { [weak self, weak page] url in
            guard let self, let page, !suspended, selectedID == page.id,
                  pages.contains(where: { $0 === page }), Self.isWebURL(url),
                  let destination = newTab() else { return false }
            destination.open(url)
            return true
        }
    }

    private static func isWebURL(_ url: URL) -> Bool {
        ["http", "https"].contains(url.scheme?.lowercased())
            && url.host?.isEmpty == false && url.absoluteString.utf8.count <= 4_096
    }
}
