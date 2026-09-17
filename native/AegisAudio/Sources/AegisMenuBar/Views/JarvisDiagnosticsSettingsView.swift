import AegisAudioCore
import SwiftUI

struct JarvisDiagnosticsSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore

    var body: some View {
        JarvisSettingsCard(title: "Estado de Jarvis") {
            statusRow("Servicio local", value: model.daemonState == .online ? "Conectado" : "Sin conexión")
            statusRow("Proveedor NVIDIA", value: store.snapshot?.credential == "configured"
                      ? "Clave configurada" : "Clave no disponible")
            statusRow("Inferencia", value: "API de NVIDIA")
            statusRow("Identidad de voz", value: model.speakerIdentityCapability == .ready ? "Activa" : "Pendiente")
            statusRow("Activación por voz", value: model.wakeWordCapability == .ready ? "Modelo disponible" : "Pendiente")
            statusRow("Atajo de voz", value: model.voiceShortcutAvailable ? JarvisVoiceShortcut.current.title : "No disponible")
            if let failure = model.lastVoiceFailureCode {
                Divider()
                Text("Último error de voz: \(failure)").font(.system(size: 11))
                    .foregroundStyle(HUDStyle.accent(for: .warning)).textSelection(.enabled)
            }
            Button("Comprobar estado", systemImage: "arrow.clockwise") {
                Task {
                    await model.refreshDaemon()
                    await model.refreshPrivacyCapabilities()
                    await store.refresh()
                }
            }
        }
        JarvisSettingsCard(title: "Aplicación") {
            statusRow("Versión", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Desarrollo")
            statusRow("Compilación", value: String(JarvisBuildIdentity.current().prefix(12)))
        }
    }
    private func statusRow(_ title: String, value: String) -> some View {
        HStack {
            Text(title).font(.system(size: 12)).foregroundStyle(Color.primary.opacity(0.65))
            Spacer()
            Text(value).font(.system(size: 12, weight: .medium)).textSelection(.enabled)
        }
    }
}
