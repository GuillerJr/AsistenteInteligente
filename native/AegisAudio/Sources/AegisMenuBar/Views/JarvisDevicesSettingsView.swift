import SwiftUI

struct JarvisDevicesSettingsView: View {
    let store: JarvisSettingsStore
    let openAccounts: () -> Void

    var body: some View {
        JarvisSettingsCard(title: "Home Assistant",
                          detail: "Jarvis utiliza la integración de Home Assistant para acceder a los dispositivos que configures allí.") {
            if let server = store.snapshot?.servers.first(where: { $0.provider == "home-assistant" }) {
                Label(server.statusTitle, systemImage: "house")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            } else {
                Label("Sin conexión configurada", systemImage: "house")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            Button("Administrar conexiones") { openAccounts() }
        }
        Text("Los dispositivos se añaden y configuran en Home Assistant. Cuando estén disponibles, conecta su perfil revisado desde Cuentas e integraciones.")
            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}
