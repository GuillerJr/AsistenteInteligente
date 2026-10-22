import AegisAudioCore
import AppKit
import Observation
import WebKit

/// One independent browser page. Web content never drives Jarvis or approves actions.
@MainActor @Observable
final class JarvisBrowserStore: NSObject, WKNavigationDelegate {
    let id = UUID()
    var address = ""
    var addressDraft = ""
    var title = ""
    var loading = false
    var canGoBack = false
    var canGoForward = false
    var failure: String?
    var externalURL: URL?
    private(set) var isClosed = false
    private(set) var documentRevision = UUID()
    let pageCapture = JarvisBrowserPageCapture()
    @ObservationIgnored let webView: WKWebView
    @ObservationIgnored var openNewTab: ((URL) -> Bool)?
    @ObservationIgnored private var titleObservation: NSKeyValueObservation?

    init(persistent: Bool = true) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = persistent ? .default() : .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        // WebKit may publish the title after didFinish and pages can change it later.
        titleObservation = webView.observe(\.title, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, !self.isClosed else { return }
                self.title = self.webView.title ?? ""
            }
        }
    }

    /// An address, a bare domain or words to search.
    static func destination(for input: String) -> URL? {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if let parsed = URL(string: text), ["http", "https"].contains(parsed.scheme?.lowercased()),
           let host = parsed.host, !host.isEmpty {
            return parsed
        } else if !text.contains(where: \.isWhitespace), !text.contains("://"),
                  !text.contains(":"), text.contains("."),
                  let parsed = URL(string: "https://\(text)"), let host = parsed.host, !host.isEmpty {
            return parsed
        } else {
            var search = URLComponents(string: "https://duckduckgo.com/")
            search?.queryItems = [URLQueryItem(name: "q", value: text)]
            return search?.url
        }
    }

    func open(_ input: String) {
        guard !isClosed, let url = Self.destination(for: input) else { return }
        invalidateDocument()
        failure = nil
        address = url.absoluteString
        addressDraft = address
        loading = true
        webView.load(URLRequest(url: url))
    }

    func open(_ url: URL) { open(url.absoluteString) }

    func goBack() {
        guard !isClosed, canGoBack else { return }
        invalidateDocument(); loading = true; webView.goBack()
    }
    func goForward() {
        guard !isClosed, canGoForward else { return }
        invalidateDocument(); loading = true; webView.goForward()
    }
    func reload() {
        guard !isClosed, webView.url != nil else { return }
        invalidateDocument(); loading = true; webView.reload()
    }
    func stopLoading() {
        guard !isClosed else { return }
        invalidateDocument()
        webView.stopLoading()
        loading = false
    }

    func deactivate() {
        pageCapture.invalidate()
        externalURL = nil
    }

    /// Closed tabs cannot accept late WebKit callbacks or retain their tab owner.
    func dispose() {
        guard !isClosed else { return }
        isClosed = true
        invalidateDocument()
        externalURL = nil
        loading = false
        openNewTab = nil
        titleObservation = nil
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
    }

    private func invalidateDocument() {
        documentRevision = UUID()
        pageCapture.invalidate()
    }

    func askAboutPage(in chat: JarvisChatStore, isSelected: @escaping @MainActor () -> Bool = { true }) {
        guard !isClosed, !loading, isSelected(), let url = webView.url else { return }
        let page = JarvisBrowserPageCapture.Page(
            revision: documentRevision, url: url, title: webView.title ?? title
        )
        pageCapture.start(page: page, to: chat, isCurrentPage: { [weak self] page in
            guard let self else { return false }
            return !isClosed && !loading && isSelected()
                && documentRevision == page.revision && webView.url == page.url
        }, readText: { [weak self] in
            guard let webView = self?.webView else { return nil }
            return await Self.pageText(in: webView)
        })
    }

    /// The visible text of the page, bounded like a chat attachment.
    func pageText() async -> String? {
        guard !isClosed else { return nil }
        return await Self.pageText(in: webView)
    }

    private static func pageText(in webView: WKWebView) async -> String? {
        guard let text = try? await webView.evaluateJavaScript("document.body ? document.body.innerText.trim().slice(0, 30000) : ''") as? String
        else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Bytes, not characters: an attachment holds at most 32 KB of text.
        return trimmed.isEmpty ? nil : String(decoding: Data(trimmed.utf8.prefix(30_000)), as: UTF8.self)
    }

    private func sync() {
        let previous = address
        address = webView.url?.absoluteString ?? address
        if addressDraft.isEmpty || addressDraft == previous { addressDraft = address }
        title = webView.title ?? ""
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard !isClosed else { return }
        invalidateDocument()
        loading = true
        sync()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard !isClosed else { return }
        loading = false
        sync()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard !isClosed else { return }
        loading = false
        sync()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard !isClosed else { return }
        loading = false
        if (error as NSError).code != NSURLErrorCancelled {
            failure = String(localized: "No pude abrir la página. Revisa la dirección o la conexión.", bundle: .module)
        }
        sync()
    }

    /// Web content cannot launch another app. Even an activated external link
    /// requires an explicit confirmation in native UI, outside the page.
    func reviewExternalNavigation(_ url: URL, activatedLink: Bool) {
        guard !isClosed else { return }
        guard activatedLink, let scheme = url.scheme?.lowercased(),
              !["file", "javascript"].contains(scheme), url.absoluteString.utf8.count <= 4_096 else {
            failure = String(localized: "La página intentó abrir otra aplicación. Abre un enlace explícito para revisarlo.", bundle: .module)
            return
        }
        externalURL = url
    }

    func confirmExternalNavigation() {
        guard !isClosed, let url = externalURL else { return }
        externalURL = nil
        NSWorkspace.shared.open(url)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard !isClosed, let url = navigationAction.request.url, let scheme = url.scheme?.lowercased() else {
            decisionHandler(.cancel)
            return
        }
        if ["http", "https", "about", "blob", "data"].contains(scheme) {
            if navigationAction.targetFrame == nil {
                reviewNewTabNavigation(url, activatedLink: navigationAction.navigationType == .linkActivated)
                decisionHandler(.cancel)
                return
            }
            if navigationAction.targetFrame?.isMainFrame != false { invalidateDocument() }
            decisionHandler(.allow)
        } else {
            reviewExternalNavigation(url, activatedLink: navigationAction.navigationType == .linkActivated)
            decisionHandler(.cancel)
        }
    }

    /// Explicit web links may create a tab; scripts cannot create pop-up tabs.
    func reviewNewTabNavigation(_ url: URL, activatedLink: Bool) {
        guard !isClosed else { return }
        guard activatedLink, ["http", "https"].contains(url.scheme?.lowercased()),
              let host = url.host, !host.isEmpty, url.absoluteString.utf8.count <= 4_096 else {
            failure = String(localized: "Se bloqueó una ventana emergente. Abre un enlace para crear una pestaña.", bundle: .module)
            return
        }
        _ = openNewTab?(url)
    }
}
