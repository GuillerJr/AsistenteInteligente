import SwiftUI

/// Keeps the standard app-menu access while using a resizable utility scene.
struct JarvisSettingsCommands: Commands {
    @Environment(\.openWindow) private var openWindow
    var interactive = true

    var body: some Commands {
        CommandGroup(replacing: .appSettings) {
            Button("Configuración…") {
                RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
            }
            .keyboardShortcut(",", modifiers: .command)
            .disabled(!interactive)
        }
    }
}
