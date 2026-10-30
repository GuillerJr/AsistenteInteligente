import DesktopCore
import SwiftUI

@main @MainActor
struct JarvisApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    private var profiles: AppProfileCoordinator { delegate.profiles }
    private var store: ChatStore { profiles.active.store }
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        WindowGroup("Jarvis", id: "main") {
            MainSceneContent(store: store, delegate: delegate)
                .id(profiles.active.runtimeID)
                .disabled(profiles.isOpening)
                .background(WindowIdentity())
                .background(SettingsSceneActionBridge(registration: delegate.settingsWindowAction))
                .onAppear {
                    delegate.store = store
                    delegate.openMainWindow = { openWindow(id: "main") }
                }
                .onChange(of: profiles.active.runtimeID) { _, _ in delegate.store = store }
        }
        .defaultSize(width: 1040, height: 720)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Nueva conversación") { let origin = store; Task { await origin.createSession(temporary: false) } }
                    .keyboardShortcut("n")
                    .disabled(!store.canChangeSession)
                Button("Chat temporal") { let origin = store; Task { await origin.createSession(temporary: true) } }
                    .keyboardShortcut("t", modifiers: [.command, .shift])
                    .disabled(!store.canChangeSession)
            }
            CommandMenu("Conversación") {
                Button("Mostrar Jarvis") { delegate.showMainWindow() }
                Button("Cancelar ejecución") { let origin = store; Task { await origin.cancel() } }
                    .keyboardShortcut(".")
                    .disabled(store.activeRunID == nil)
            }
        }
        MenuBarExtra("Jarvis", systemImage: "sparkle") {
            Button("Abrir Jarvis") { delegate.showMainWindow() }
            Button("Nueva conversación") {
                let origin = store
                delegate.showMainWindow()
                Task { await origin.createSession(temporary: false) }
            }.disabled(!store.canChangeSession)
            Divider()
            Text(store.connected ? "ChatGPT conectado" : "Sin conexión a ChatGPT")
            SettingsLink { Text("Ajustes…") }
            Button("Salir de Jarvis") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }
        Settings { SettingsView(store: store, delegate: delegate, profiles: profiles).id(profiles.active.runtimeID).disabled(profiles.isOpening) }
    }
}

/// The pointer is outside ContentView so it is present in both ordinary chat
/// and recovery-only launches. Opening it uses the same app route as a reopen.
@MainActor private struct MainSceneContent: View {
    let store: ChatStore
    let delegate: AppDelegate
    private var notices: [OwnerDecisionNotice] { delegate.ownerDecisionNotices(for: store) }
    private var navigation: SettingsOwnerNavigation { delegate.settingsNavigation }

    var body: some View {
        VStack(spacing: 0) {
            if let notice = navigation.attention(in: notices) {
                OwnerDecisionBanner(notice: notice, navigation: navigation, onReveal: delegate.showMainWindow)
            }
            ContentView(store: store)
        }
        .onAppear { navigation.configure(runtimeID: store.runtime.id); navigation.detect(notices, visible: false) }
        .onChange(of: notices) { _, rows in navigation.detect(rows, visible: false) }
    }
}

private struct WindowIdentity: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = IdentityView()
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}

    private final class IdentityView: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            window?.identifier = NSUserInterfaceItemIdentifier("jarvis.main")
        }
    }
}
