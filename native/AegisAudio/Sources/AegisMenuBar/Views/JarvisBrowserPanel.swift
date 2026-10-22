import AegisAudioCore
import AppKit
import SwiftUI
import WebKit

private struct JarvisWebView: NSViewRepresentable {
    let webView: WKWebView
    func makeNSView(context: Context) -> WKWebView { webView }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

struct JarvisBrowserPanel: View {
    let tabs: JarvisBrowserTabs
    let chat: JarvisChatStore
    let isVisible: @MainActor () -> Bool
    let close: () -> Void

    init(tabs: JarvisBrowserTabs, chat: JarvisChatStore,
         isVisible: @escaping @MainActor () -> Bool = { true }, close: @escaping () -> Void) {
        self.tabs = tabs
        self.chat = chat
        self.isVisible = isVisible
        self.close = close
    }

    init(store: JarvisBrowserStore, chat: JarvisChatStore, close: @escaping () -> Void) {
        self.init(tabs: JarvisBrowserTabs(initialPage: store,
            makePage: { JarvisBrowserStore(persistent: false) }), chat: chat, close: close)
    }

    var body: some View {
        VStack(spacing: 0) {
            JarvisBrowserTabStrip(tabs: tabs)
            JarvisSeparator()
            if let notice = tabs.notice {
                Text(notice).font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            }
            JarvisBrowserPagePanel(tabs: tabs, store: tabs.selected, chat: chat,
                                  isVisible: isVisible, close: close)
                .id(tabs.selectedID)
        }
        .background(JarvisTheme.sidebar)
        .onAppear { tabs.resume() }
        .onDisappear { tabs.suspend() }
        .onChange(of: chat.conversationID) { _, _ in tabs.invalidateCaptures() }
        .onChange(of: chat.transcriptRevision) { _, _ in tabs.invalidateCaptures() }
    }
}

private struct JarvisBrowserPagePanel: View {
    let tabs: JarvisBrowserTabs
    let store: JarvisBrowserStore
    let chat: JarvisChatStore
    let isVisible: @MainActor () -> Bool
    let close: () -> Void
    @FocusState private var addressFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                Button { store.goBack() } label: { Image(systemName: "chevron.left") }
                    .disabled(!store.canGoBack).help(String(localized: "Atrás", bundle: .module)).accessibilityLabel(String(localized: "Atrás", bundle: .module))
                Button { store.goForward() } label: { Image(systemName: "chevron.right") }
                    .disabled(!store.canGoForward).help(String(localized: "Adelante", bundle: .module)).accessibilityLabel(String(localized: "Adelante", bundle: .module))
                Button {
                    if store.loading { store.stopLoading() } else { store.reload() }
                } label: {
                    Image(systemName: store.loading ? "xmark" : "arrow.clockwise")
                }.help(store.loading ? String(localized: "Detener", bundle: .module) : String(localized: "Recargar", bundle: .module))
                    .accessibilityLabel(store.loading ? String(localized: "Detener carga", bundle: .module) : String(localized: "Recargar página", bundle: .module))
                TextField(String(localized: "Busca o escribe una dirección", bundle: .module), text: Binding(get: { store.addressDraft }, set: { store.addressDraft = $0 }))
                    .textFieldStyle(.plain).font(.system(size: 12.5)).focused($addressFocused)
                    .padding(.horizontal, 10).frame(height: 28)
                    .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 7))
                    .onSubmit { store.open(store.addressDraft); addressFocused = false }
                    .accessibilityLabel(String(localized: "Dirección", bundle: .module))
                Button {
                    if let url = store.webView.url { NSWorkspace.shared.open(url) }
                } label: { Image(systemName: "safari") }
                    .disabled(store.webView.url == nil).help(String(localized: "Abrir en tu navegador", bundle: .module))
                    .accessibilityLabel(String(localized: "Abrir en tu navegador", bundle: .module))
                Button { tabs.suspend(); close() } label: { Image(systemName: "xmark") }.help(String(localized: "Cerrar navegador", bundle: .module))
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
                tabs.askAboutSelectedPage(in: chat, isVisible: isVisible)
            } label: {
                Label(String(localized: "Preguntar a Jarvis sobre esta página", bundle: .module), systemImage: "text.bubble")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(JarvisButtonStyle(compact: true))
            .disabled(store.webView.url == nil || store.loading || store.pageCapture.isReading
                || chat.isEditing || chat.attachments.count >= 4)
            .padding(10)
        }
        .frame(minWidth: 340, idealWidth: 460)
        .background(JarvisTheme.sidebar)
        .onAppear { if store.webView.url == nil { addressFocused = true } }
        .onDisappear { store.deactivate() }
        .background {
            Button(String(localized: "Ir a la dirección", bundle: .module)) { addressFocused = true }
                .keyboardShortcut("l", modifiers: .command).hidden().accessibilityHidden(true)
        }
        .alert(String(localized: "¿Abrir otra aplicación?", bundle: .module), isPresented: Binding(
            get: { store.externalURL != nil }, set: { if !$0 { store.externalURL = nil } }
        )) {
            Button(String(localized: "Abrir aplicación", bundle: .module)) { store.confirmExternalNavigation() }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) { store.externalURL = nil }
        } message: {
            Text(String(localized: "Este enlace sale de Jarvis. Revisa la dirección antes de continuar:\n\(store.externalURL?.absoluteString ?? "")", bundle: .module))
        }
    }

}
