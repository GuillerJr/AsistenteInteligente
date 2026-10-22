import AppKit
import SwiftUI

/// Keeps the standard app-menu access while using a resizable utility scene.
struct JarvisSettingsCommands: Commands {
    @Environment(\.openWindow) private var openWindow
    let model: MenuBarModel
    var interactive = true

    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            Button(String(localized: "Acerca de Jarvis", bundle: .module)) { JarvisBuildInfo.showAbout() }
        }
        CommandGroup(replacing: .help) {
            Button(String(localized: "Manual de Jarvis", bundle: .module)) {
                if let url = Bundle.main.url(forResource: "MANUAL_FACIL", withExtension: "md") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
        CommandGroup(replacing: .appSettings) {
            Button(String(localized: "Configuración…", bundle: .module)) {
                RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
            }
            .keyboardShortcut(",", modifiers: .command)
            .disabled(!interactive)
        }
        CommandMenu(String(localized: "Conversación", bundle: .module)) {
            Button(String(localized: "Nuevo chat", bundle: .module)) { model.chat.newConversation(); showChat() }
                .keyboardShortcut("n", modifiers: .command)
                .disabled(!interactive || !model.canManageChatHistory)
            Button(String(localized: "Chat temporal", bundle: .module)) { model.chat.newTemporaryConversation(); showChat() }
                .keyboardShortcut("t", modifiers: [.command, .shift])
                .disabled(!interactive || !model.canManageChatHistory)
            Toggle(String(localized: "Modo plan", bundle: .module), isOn: Binding(get: { model.chat.planMode }, set: { model.chat.planMode = $0 }))
                .keyboardShortcut("p", modifiers: [.command, .shift]).disabled(!interactive)
            Button(String(localized: "Buscar chats", bundle: .module)) {
                model.chat.showHistory = true
                model.chat.searchFocusPending = true
                model.chat.searchFocusRequest = UUID()
                showChat()
            }.keyboardShortcut("f", modifiers: .command).disabled(!interactive)
            Button(String(localized: "Abrir navegador", bundle: .module)) {
                model.chat.pane = .chat
                model.chat.browserPending = true
                model.chat.browserRequest = UUID()
                showChat()
            }.keyboardShortcut("b", modifiers: [.command, .option]).disabled(!interactive)
            Divider()
            Button(model.chat.currentConversation?.filed == true ? String(localized: "Desarchivar chat", bundle: .module) : String(localized: "Archivar chat", bundle: .module)) {
                guard let entry = model.chat.currentConversation else { return }
                Task { await model.chat.setFiled(entry, model: model, filed: !entry.filed) }
            }.keyboardShortcut("a", modifiers: [.command, .control])
                .disabled(!interactive || !model.canManageChatHistory || model.chat.currentConversation == nil)
            Button(String(localized: "Eliminar chat…", bundle: .module)) {
                model.chat.deletePending = true
                model.chat.deleteRequest = UUID()
                showChat()
            }.keyboardShortcut(.delete, modifiers: [.command, .shift])
                .disabled(!interactive || !model.canManageChatHistory || model.chat.currentConversation == nil)
            Divider()
            Button(String(localized: "Abrir panel de Jarvis", bundle: .module)) {
                NotchPanelController.shared.togglePresentation()
            }.keyboardShortcut("n", modifiers: [.command, .shift]).disabled(!interactive)
            Button(String(localized: "Abrir chat", bundle: .module)) {
                RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
            }.keyboardShortcut("j", modifiers: [.command, .shift]).disabled(!interactive)
        }
    }

    private func showChat() {
        RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
    }
}
