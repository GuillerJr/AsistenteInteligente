import AegisAudioCore
import ServiceManagement
import SwiftUI

struct JarvisGeneralSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    @State private var loginEnabled = SMAppService.mainApp.status == .enabled
    @State private var shortcut = JarvisVoiceShortcut.current

    var body: some View {
        JarvisSettingsCard(title: "Inicio y accesos") {
            Toggle("Abrir Jarvis al iniciar sesión", isOn: Binding(
                get: { loginEnabled }, set: { value in
                    do {
                        if value { try SMAppService.mainApp.register() }
                        else { try SMAppService.mainApp.unregister() }
                        loginEnabled = SMAppService.mainApp.status == .enabled
                        if SMAppService.mainApp.status == .requiresApproval {
                            SMAppService.openSystemSettingsLoginItems()
                            store.message = "Aprueba Jarvis en los ítems de inicio de macOS."
                        }
                    } catch {
                        loginEnabled = SMAppService.mainApp.status == .enabled
                        store.fail("macOS no pudo cambiar el inicio automático. Usa la app instalada en Aplicaciones.")
                    }
                }
            ))
            .toggleStyle(JarvisSwitchStyle())
            Divider()
            Picker("Atajo para hablar", selection: Binding(get: { shortcut }, set: { value in
                if VoiceHotKeyController.shared.setShortcut(value) {
                    shortcut = value
                    model.voiceShortcutAvailable = true
                } else {
                    store.fail("No se pudo registrar ese atajo. Se conservó el anterior.")
                }
            })) {
                ForEach(JarvisVoiceShortcut.allCases) { option in Text(option.title).tag(option) }
            }
            .pickerStyle(.menu)
        }
        JarvisSettingsCard(title: "Avisos", detail: "Batería, red, agenda y rendimiento.") {
            Toggle("Alertas proactivas", isOn: Binding(
                get: { model.proactiveAlertsEnabled },
                set: { value in Task { await model.setProactiveAlertsEnabled(value) } }
            ))
            .toggleStyle(JarvisSwitchStyle())
        }
        JarvisSettingsCard(title: "Apariencia", detail: "Jarvis mantiene su diseño oscuro. Respeta las opciones de reducir transparencia y aumentar contraste de macOS.") {
            Label("Estilo de Jarvis", systemImage: "circle.lefthalf.filled")
                .font(.system(size: 12)).foregroundStyle(Color.primary.opacity(0.7))
        }
    }
}
