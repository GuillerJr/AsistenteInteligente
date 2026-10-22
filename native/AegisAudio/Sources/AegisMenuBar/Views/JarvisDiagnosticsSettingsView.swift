import AegisAudioCore
import SwiftUI

struct JarvisDiagnosticsSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    let restart: @MainActor () async throws -> Void

    var body: some View {
        JarvisHealthView(model: model, restart: restart)
        JarvisSettingsCard(title: String(localized: "Estado de Jarvis", bundle: .module)) {
            statusRow(String(localized: "Servicio local", bundle: .module), value: model.daemonState == .online ? String(localized: "Conectado", bundle: .module) : String(localized: "Sin conexión", bundle: .module))
            statusRow(String(localized: "Proveedor NVIDIA", bundle: .module), value: store.snapshot?.credential == "configured"
                      ? String(localized: "Clave configurada", bundle: .module) : String(localized: "Clave no disponible", bundle: .module))
            statusRow(String(localized: "Inferencia", bundle: .module), value: String(localized: "API de NVIDIA", bundle: .module))
            statusRow(String(localized: "Identidad de voz", bundle: .module), value: model.speakerIdentityCapability == .ready ? String(localized: "Activa", bundle: .module) : String(localized: "Pendiente", bundle: .module))
            statusRow(String(localized: "Activación por voz", bundle: .module), value: model.wakeWordCapability == .ready ? String(localized: "Modelo disponible", bundle: .module) : String(localized: "Pendiente", bundle: .module))
            statusRow(String(localized: "Atajo de voz", bundle: .module), value: model.voiceShortcutAvailable ? JarvisVoiceShortcut.current.title : String(localized: "No disponible", bundle: .module))
            if let failure = model.lastVoiceFailureCode {
                Divider()
                Text(String(localized: "Último error de voz: \(failure)", bundle: .module)).font(.system(size: 11))
                    .foregroundStyle(HUDStyle.accent(for: .warning)).textSelection(.enabled)
            }
            Button(String(localized: "Comprobar estado", bundle: .module), systemImage: "arrow.clockwise") {
                Task {
                    await model.refreshDaemon()
                    await model.refreshPrivacyCapabilities()
                    await store.refresh()
                }
            }
        }
        JarvisSettingsCard(title: String(localized: "Aplicación", bundle: .module)) {
            statusRow(String(localized: "Versión", bundle: .module), value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? String(localized: "Desarrollo", bundle: .module))
            statusRow(String(localized: "Compilación", bundle: .module), value: String(JarvisBuildIdentity.current().prefix(12)))
        }
    }
    private func statusRow(_ title: String, value: String) -> some View {
        HStack {
            Text(title).font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            Spacer()
            Text(value).font(.system(size: 12, weight: .medium)).textSelection(.enabled)
        }
    }
}
