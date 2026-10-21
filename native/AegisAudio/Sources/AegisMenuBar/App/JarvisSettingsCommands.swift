import AppKit
import SwiftUI

/// Keeps the standard app-menu access while using a resizable utility scene.
struct JarvisSettingsCommands: Commands {
    @Environment(\.openWindow) private var openWindow
    var interactive = true

    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            Button("Acerca de Jarvis") { NSApp.orderFrontStandardAboutPanel(nil) }
        }
        CommandGroup(replacing: .help) {
            Button("Manual de Jarvis") {
                if let url = Bundle.main.url(forResource: "MANUAL_FACIL", withExtension: "md") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
        CommandGroup(replacing: .appSettings) {
            Button("Configuración…") {
                RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
            }
            .keyboardShortcut(",", modifiers: .command)
            .disabled(!interactive)
        }
        CommandMenu("Conversación") {
            Button("Abrir panel de Jarvis") {
                NotchPanelController.shared.togglePresentation()
            }.keyboardShortcut("n", modifiers: [.command, .shift]).disabled(!interactive)
            Button("Abrir chat") {
                RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
            }.keyboardShortcut("j", modifiers: [.command, .shift]).disabled(!interactive)
        }
    }
}
