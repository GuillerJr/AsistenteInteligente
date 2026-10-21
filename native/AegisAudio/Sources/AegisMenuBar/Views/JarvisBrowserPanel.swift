import AegisAudioCore
import AppKit
import SwiftUI
import WebKit

/// The chat window's built-in browser (Codex's in-app browser): a WebKit page beside
/// the conversation. Links in Jarvis's replies open here, and the owner can hand the
/// page's text to Jarvis as an attachment; the page itself never drives Jarvis.
@MainActor @Observable
final class JarvisBrowserStore: NSObject, WKNavigationDelegate {
    var address = ""
    var title = ""
    var loading = false
    var canGoBack = false
    var canGoForward = false
    var failure: String?
    @ObservationIgnored let webView: WKWebView

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = true
    }

    /// An address, a bare domain or words to search.
    func open(_ input: String) {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let url: URL?
        if let parsed = URL(string: text), ["http", "https"].contains(parsed.scheme?.lowercased()) {
            url = parsed
        } else if !text.contains(" "), text.contains("."), let parsed = URL(string: "https://\(text)") {
            url = parsed
        } else {
            var search = URLComponents(string: "https://duckduckgo.com/")
            search?.queryItems = [URLQueryItem(name: "q", value: text)]
            url = search?.url
        }
        guard let url else { return }
        failure = nil
        webView.load(URLRequest(url: url))
    }

    func open(_ url: URL) { open(url.absoluteString) }

    /// The visible text of the page, bounded like a chat attachment.
    func pageText() async -> String? {
        guard let text = try? await webView.evaluateJavaScript("document.body ? document.body.innerText : ''") as? String
        else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Bytes, not characters: an attachment holds at most 32 KB of text.
        return trimmed.isEmpty ? nil : String(decoding: Data(trimmed.utf8.prefix(30_000)), as: UTF8.self)
    }

    private func sync() {
        address = webView.url?.absoluteString ?? address
        title = webView.title ?? ""
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        loading = true
        sync()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading = false
        sync()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        loading = false
        sync()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loading = false
        if (error as NSError).code != NSURLErrorCancelled {
            failure = "No pude abrir la página. Revisa la dirección o la conexión."
        }
        sync()
    }

    /// Only web pages load here; mail, phone and other schemes go to their apps.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let scheme = url.scheme?.lowercased() else {
            decisionHandler(.cancel)
            return
        }
        if ["http", "https", "about", "blob", "data"].contains(scheme) {
            if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
            decisionHandler(navigationAction.targetFrame == nil ? .cancel : .allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }
}

private struct JarvisWebView: NSViewRepresentable {
    let webView: WKWebView
    func makeNSView(context: Context) -> WKWebView { webView }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

struct JarvisBrowserPanel: View {
    let store: JarvisBrowserStore
    let chat: JarvisChatStore
    let close: () -> Void
    @State private var draft = ""
    @FocusState private var addressFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                Button { store.webView.goBack() } label: { Image(systemName: "chevron.left") }
                    .disabled(!store.canGoBack).help("Atrás")
                Button { store.webView.goForward() } label: { Image(systemName: "chevron.right") }
                    .disabled(!store.canGoForward).help("Adelante")
                Button {
                    if store.loading { store.webView.stopLoading() } else { _ = store.webView.reload() }
                } label: {
                    Image(systemName: store.loading ? "xmark" : "arrow.clockwise")
                }.help(store.loading ? "Detener" : "Recargar")
                TextField("Busca o escribe una dirección", text: $draft)
                    .textFieldStyle(.plain).font(.system(size: 12.5)).focused($addressFocused)
                    .padding(.horizontal, 10).frame(height: 28)
                    .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 7))
                    .onSubmit { store.open(draft); addressFocused = false }
                    .accessibilityLabel("Dirección")
                Button {
                    if let url = store.webView.url { NSWorkspace.shared.open(url) }
                } label: { Image(systemName: "safari") }
                    .disabled(store.webView.url == nil).help("Abrir en tu navegador")
                Button(action: close) { Image(systemName: "xmark") }.help("Cerrar navegador")
            }
            .buttonStyle(JarvisIconButtonStyle())
            .padding(.horizontal, 10).frame(height: 52)
            JarvisSeparator()
            ZStack {
                JarvisWebView(webView: store.webView)
                if store.webView.url == nil && !store.loading {
                    VStack(spacing: 8) {
                        Image(systemName: "globe").font(.system(size: 26)).foregroundStyle(JarvisTheme.inkMuted)
                        Text("Empieza a navegar").font(JarvisTheme.voice(18))
                        Text("Los enlaces de Jarvis se abren aquí.").font(.system(size: 12))
                            .foregroundStyle(JarvisTheme.inkSecondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity).background(JarvisTheme.canvas)
                }
                if let failure = store.failure {
                    Text(failure).font(.system(size: 12)).padding(10)
                        .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: 8))
                        .frame(maxHeight: .infinity, alignment: .top).padding(.top, 12)
                }
            }
            JarvisSeparator()
            Button {
                Task { await askAboutPage() }
            } label: {
                Label("Preguntar a Jarvis sobre esta página", systemImage: "text.bubble")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(JarvisButtonStyle(compact: true))
            .disabled(store.webView.url == nil || store.loading || chat.attachments.count >= 4)
            .padding(10)
        }
        .frame(minWidth: 340, idealWidth: 460)
        .background(JarvisTheme.sidebar)
        .onChange(of: store.address) { _, value in if !addressFocused { draft = value } }
        .onAppear { draft = store.address; if store.webView.url == nil { addressFocused = true } }
    }

    private func askAboutPage() async {
        guard let text = await store.pageText(), let url = store.webView.url else {
            chat.error = "Esta página no tiene texto que Jarvis pueda leer."
            return
        }
        let name = String((store.title.isEmpty ? url.host() ?? "página" : store.title).prefix(80))
        let document = ChatDocumentAttachment(name: "\(name).txt", content: "Página: \(url.absoluteString)\n\n\(text)")
        let combined = chat.attachments + [ChatAttachment(document: document)]
        do {
            try ChatAttachmentLoader.validate(combined)
            chat.attachments = combined
        } catch {
            chat.error = error.localizedDescription
        }
    }
}
