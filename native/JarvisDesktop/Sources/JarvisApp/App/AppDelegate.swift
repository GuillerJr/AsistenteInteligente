import AppKit
import Carbon

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var store: ChatStore?
    var openMainWindow: (() -> Void)?
    var shortcutError: String?
    private var hotKey: EventHotKeyRef?
    private var eventHandler: EventHandlerRef?
    private var terminating = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        installShortcut(UserDefaults.standard.string(forKey: "jarvis.openShortcut") ?? "commandShiftJ")
    }

    func showMainWindow() {
        if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "jarvis.main" }) {
            window.makeKeyAndOrderFront(nil)
        } else { openMainWindow?() }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateLater }
        terminating = true
        Task {
            let graceful = await store?.shutdown() ?? true
            if !graceful {
                let alert = NSAlert()
                alert.messageText = "El servicio no terminó de cerrar a tiempo"
                alert.informativeText = "Jarvis forzó el cierre. Una ejecución pendiente puede haber quedado interrumpida; se consultará su registro al volver a abrir. No se repetirá automáticamente."
                alert.addButton(withTitle: "Cerrar Jarvis")
                alert.runModal()
            }
            if let hotKey { UnregisterEventHotKey(hotKey) }
            if let eventHandler { RemoveEventHandler(eventHandler) }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func installShortcut(_ choice: String) {
        if let hotKey { UnregisterEventHotKey(hotKey); self.hotKey = nil }
        shortcutError = nil
        guard choice != "disabled" else { return }
        if eventHandler == nil {
            var type = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
            let context = Unmanaged.passUnretained(self).toOpaque()
            let result = InstallEventHandler(GetApplicationEventTarget(), { _, _, context in
                guard let context else { return OSStatus(eventNotHandledErr) }
                let delegate = Unmanaged<AppDelegate>.fromOpaque(context).takeUnretainedValue()
                Task { @MainActor in delegate.showMainWindow() }
                return noErr
            }, 1, &type, context, &eventHandler)
            guard result == noErr else { shortcutError = "No se pudo registrar el atajo."; return }
        }
        let key = choice == "optionSpace" ? UInt32(kVK_Space) : UInt32(kVK_ANSI_J)
        let modifiers = choice == "optionSpace" ? UInt32(optionKey) : UInt32(cmdKey | shiftKey)
        let result = RegisterEventHotKey(key, modifiers, EventHotKeyID(signature: 0x4a415256, id: 1),
                                         GetApplicationEventTarget(), 0, &hotKey)
        if result != noErr { shortcutError = "El atajo está ocupado. Elige otro en Ajustes." }
    }
}
