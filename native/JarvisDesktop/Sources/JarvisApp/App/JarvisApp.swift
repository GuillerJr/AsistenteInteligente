import SwiftUI

@main @MainActor
struct JarvisApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var store = ChatStore()
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        WindowGroup("Jarvis", id: "main") {
            ContentView(store: store)
                .background(WindowIdentity())
                .onAppear {
                    delegate.store = store
                    delegate.openMainWindow = { openWindow(id: "main") }
                }
                .task { await store.start() }
        }
        .defaultSize(width: 1040, height: 720)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Nueva conversación") { Task { await store.createSession(temporary: false) } }
                    .keyboardShortcut("n")
                    .disabled(!store.canChangeSession)
                Button("Chat temporal") { Task { await store.createSession(temporary: true) } }
                    .keyboardShortcut("t", modifiers: [.command, .shift])
                    .disabled(!store.canChangeSession)
            }
            CommandMenu("Conversación") {
                Button("Mostrar Jarvis") { delegate.showMainWindow() }
                Button("Cancelar ejecución") { Task { await store.cancel() } }
                    .keyboardShortcut(".")
                    .disabled(store.activeRunID == nil)
            }
        }
        MenuBarExtra("Jarvis", systemImage: "sparkle") {
            Button("Abrir Jarvis") { delegate.showMainWindow() }
            Button("Nueva conversación") {
                delegate.showMainWindow()
                Task { await store.createSession(temporary: false) }
            }.disabled(!store.canChangeSession)
            Divider()
            Text(store.connected ? "ChatGPT conectado" : "Sin conexión a ChatGPT")
            SettingsLink { Text("Ajustes…") }
            Button("Salir de Jarvis") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }
        Settings { SettingsView(store: store, delegate: delegate) }
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
