import SwiftUI

@main @MainActor
struct JarvisApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var profiles = AppProfileCoordinator()
    private var store: ChatStore { profiles.active.store }
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        WindowGroup("Jarvis", id: "main") {
            ContentView(store: store)
                .id(profiles.active.runtimeID)
                .disabled(profiles.isOpening)
                .background(WindowIdentity())
                .onAppear {
                    delegate.store = store
                    delegate.profiles = profiles
                    delegate.openMainWindow = { openWindow(id: "main") }
                }
                .onChange(of: profiles.active.runtimeID) { _, _ in delegate.store = store }
                .task { await profiles.start() }
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
