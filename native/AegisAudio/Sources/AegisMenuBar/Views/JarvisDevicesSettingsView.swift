import SwiftUI

struct JarvisDevicesSettingsView: View {
    let store: JarvisSettingsStore
    let openAccounts: () -> Void

    var body: some View {
        JarvisSettingsCard(title: String(localized: "Home Assistant", bundle: .module),
                          detail: String(localized: "Jarvis utiliza la integración de Home Assistant para acceder a los dispositivos que configures allí.", bundle: .module)) {
            if let server = store.snapshot?.servers.first(where: { $0.provider == "home-assistant" }) {
                Label(server.statusTitle, systemImage: "house")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            } else {
                Label(String(localized: "Sin conexión configurada", bundle: .module), systemImage: "house")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            Button(String(localized: "Administrar conexiones", bundle: .module)) { openAccounts() }
        }
        Text(String(localized: "Los dispositivos se añaden y configuran en Home Assistant. Cuando estén disponibles, conecta su perfil revisado desde Cuentas e integraciones.", bundle: .module))
            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}
