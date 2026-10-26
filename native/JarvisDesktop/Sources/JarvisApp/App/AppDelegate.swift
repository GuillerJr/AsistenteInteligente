import AppKit
import Carbon
import DesktopCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var store: ChatStore?
    var profiles: AppProfileCoordinator?
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
        presentWindow(preserveVisibleOnReopen: false)
    }

    private func presentWindow(preserveVisibleOnReopen: Bool) {
        let ordered = NSApp.orderedWindows
        let windows = ordered + NSApp.windows.filter { candidate in !ordered.contains { $0 === candidate } }
        let pending = OwnerDecisionWindowPolicy.awaitingOwner([
            store?.recovery.operation?.phase, store?.maintenance.operation?.phase,
            store?.memoryRestoration.operation?.phase, store?.restored.operation?.phase,
        ])
        if let window = OwnerDecisionWindowPolicy.target(windows: windows, modal: NSApp.modalWindow,
            key: NSApp.keyWindow, awaitingOwner: pending, preserveVisibleOnReopen: preserveVisibleOnReopen) {
            window.makeKeyAndOrderFront(nil)
        } else { openMainWindow?() }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        presentWindow(preserveVisibleOnReopen: flag)
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateLater }
        terminating = true
        Task {
            let confirmed: Bool
            if let profiles { confirmed = await profiles.shutdown() }
            else { confirmed = await store?.shutdown() ?? true }
            if !confirmed {
                let alert = NSAlert()
                alert.messageText = "No se confirmó el cierre del servicio"
                alert.informativeText = "Jarvis conserva la supervisión del proceso y no iniciará otro servicio. Puedes volver a elegir Salir para reintentar el cierre."
                alert.addButton(withTitle: "Conservar Jarvis abierto")
                alert.runModal()
                terminating = false
                NSApp.reply(toApplicationShouldTerminate: false)
                return
            }
            if let warning = profiles?.shutdownWarning ?? store?.shutdownWarning {
                let alert = NSAlert()
                alert.messageText = "El servicio terminó de forma no habitual"
                alert.informativeText = warning
                alert.addButton(withTitle: "Salir de Jarvis")
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
