import SwiftUI

struct SettingsView: View {
    let store: ChatStore
    let delegate: AppDelegate
    @AppStorage("jarvis.openShortcut") private var shortcut = "commandShiftJ"

    var body: some View {
        Form {
            Section("Escritorio") {
                Picker("Abrir Jarvis", selection: $shortcut) {
                    Text("⌘⇧J").tag("commandShiftJ")
                    Text("⌥Espacio").tag("optionSpace")
                    Text("Desactivado").tag("disabled")
                }
                .onChange(of: shortcut) { _, value in delegate.installShortcut(value) }
                if let error = delegate.shortcutError { Text(error).foregroundStyle(.red) }
                Text("Cerrar la ventana mantiene Jarvis en la barra de menús. Usa «Salir de Jarvis» para cerrar también el servicio.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Cuenta de ChatGPT") {
                Text(store.statusText)
                HStack {
                    Button("Abrir inicio de sesión") { Task { await store.login() } }.disabled(!store.ready)
                    Button("Cerrar sesión") { Task { await store.logout() } }
                        .disabled(!store.connected || store.activeRunID != nil)
                }
            }
            Section("Estado de esta base") {
                Text("Cliente nativo de desarrollo. Requiere el servicio Python del repositorio y el cliente oficial de Codex.")
                Text("Las acciones en otras apps, audio y conectores se integrarán en una fase posterior.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped).frame(width: 580, height: 430)
    }
}
