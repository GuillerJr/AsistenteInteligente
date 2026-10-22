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
    var externalURL: URL?
    @ObservationIgnored let webView: WKWebView

    init(persistent: Bool = true) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = persistent ? .default() : .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = true
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
        guard let url = Self.destination(for: input) else { return }
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
            failure = String(localized: "No pude abrir la página. Revisa la dirección o la conexión.", bundle: .module)
        }
        sync()
    }

    /// Web content cannot launch another app. Even an activated external link
    /// requires an explicit confirmation in native UI, outside the page.
    func reviewExternalNavigation(_ url: URL, activatedLink: Bool) {
        guard activatedLink, let scheme = url.scheme?.lowercased(),
              !["file", "javascript"].contains(scheme), url.absoluteString.utf8.count <= 4_096 else {
            failure = String(localized: "La página intentó abrir otra aplicación. Abre un enlace explícito para revisarlo.", bundle: .module)
            return
        }
        externalURL = url
    }

    func confirmExternalNavigation() {
        guard let url = externalURL else { return }
        externalURL = nil
        NSWorkspace.shared.open(url)
    }

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
            reviewExternalNavigation(url, activatedLink: navigationAction.navigationType == .linkActivated)
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
                    .disabled(!store.canGoBack).help(String(localized: "Atrás", bundle: .module)).accessibilityLabel(String(localized: "Atrás", bundle: .module))
                Button { store.webView.goForward() } label: { Image(systemName: "chevron.right") }
                    .disabled(!store.canGoForward).help(String(localized: "Adelante", bundle: .module)).accessibilityLabel(String(localized: "Adelante", bundle: .module))
                Button {
                    if store.loading { store.webView.stopLoading() } else { _ = store.webView.reload() }
                } label: {
                    Image(systemName: store.loading ? "xmark" : "arrow.clockwise")
                }.help(store.loading ? String(localized: "Detener", bundle: .module) : String(localized: "Recargar", bundle: .module))
                    .accessibilityLabel(store.loading ? String(localized: "Detener carga", bundle: .module) : String(localized: "Recargar página", bundle: .module))
                TextField(String(localized: "Busca o escribe una dirección", bundle: .module), text: $draft)
                    .textFieldStyle(.plain).font(.system(size: 12.5)).focused($addressFocused)
                    .padding(.horizontal, 10).frame(height: 28)
                    .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 7))
                    .onSubmit { store.open(draft); addressFocused = false }
                    .accessibilityLabel(String(localized: "Dirección", bundle: .module))
                Button {
                    if let url = store.webView.url { NSWorkspace.shared.open(url) }
                } label: { Image(systemName: "safari") }
                    .disabled(store.webView.url == nil).help(String(localized: "Abrir en tu navegador", bundle: .module))
                    .accessibilityLabel(String(localized: "Abrir en tu navegador", bundle: .module))
                Button(action: close) { Image(systemName: "xmark") }.help(String(localized: "Cerrar navegador", bundle: .module))
                    .accessibilityLabel(String(localized: "Cerrar navegador", bundle: .module)).keyboardShortcut(.escape, modifiers: [])
            }
            .buttonStyle(JarvisIconButtonStyle())
            .padding(.horizontal, 10).frame(height: 52)
            JarvisSeparator()
            ZStack {
                // An unloaded WebKit view traps keyboard focus behind the empty-state
                // overlay. Join the key loop only once it is displaying a page.
                if store.webView.url != nil || store.loading {
                    JarvisWebView(webView: store.webView)
                }
                if store.webView.url == nil && !store.loading {
                    VStack(spacing: 8) {
                        Image(systemName: "globe").font(.system(size: 26)).foregroundStyle(JarvisTheme.inkMuted)
                        Text(String(localized: "Empieza a navegar", bundle: .module)).font(JarvisTheme.voice(18))
                        Text(String(localized: "Los enlaces de Jarvis se abren aquí.", bundle: .module)).font(.system(size: 12))
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
                Label(String(localized: "Preguntar a Jarvis sobre esta página", bundle: .module), systemImage: "text.bubble")
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
        .alert(String(localized: "¿Abrir otra aplicación?", bundle: .module), isPresented: Binding(
            get: { store.externalURL != nil }, set: { if !$0 { store.externalURL = nil } }
        )) {
            Button(String(localized: "Abrir aplicación", bundle: .module)) { store.confirmExternalNavigation() }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) { store.externalURL = nil }
        } message: {
            Text(String(localized: "Este enlace sale de Jarvis. Revisa la dirección antes de continuar:\n\(store.externalURL?.absoluteString ?? "")", bundle: .module))
        }
    }

    private func askAboutPage() async {
        guard let text = await store.pageText(), let url = store.webView.url else {
            chat.error = String(localized: "Esta página no tiene texto que Jarvis pueda leer.", bundle: .module)
            return
        }
        let name = String((store.title.isEmpty ? url.host() ?? String(localized: "página", bundle: .module) : store.title).prefix(80))
        let document = ChatDocumentAttachment(name: "\(name).txt", content: String(localized: "Página: \(url.absoluteString)\n\n\(text)", bundle: .module))
        let combined = chat.attachments + [ChatAttachment(document: document)]
        do {
            try ChatAttachmentLoader.validate(combined)
            chat.attachments = combined
        } catch {
            chat.error = error.localizedDescription
        }
    }
}
