import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct JarvisAccountsSettingsView: View {
    let store: JarvisSettingsStore
    let present: (JarvisSettingsSheet) -> Void
    @State private var confirmation: IntegrationConfirmation?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
        if let snapshot = store.snapshot {
            JarvisSettingsCard(title: String(localized: "Navegador", bundle: .module),
                              detail: String(localized: "Elige cómo abrir las tareas web. El acceso y el análisis con NVIDIA se aprobarán para cada tarea.", bundle: .module)) {
                if let browser = snapshot.browser_automation {
                    Toggle(String(localized: "Usar navegador en tareas", bundle: .module), isOn: Binding(
                        get: { browser.enabled }, set: { value in
                            Task { await store.saveBrowserAutomation(enabled: value, mode: browser.mode) }
                        }
                    ))
                    .toggleStyle(JarvisSwitchStyle())
                    Text(browser.connection == "invalid"
                         ? String(localized: "No se pudo validar la preferencia guardada. Revisa la configuración antes de usar el navegador.", bundle: .module)
                         : "\(browser.modeTitle) · \(browser.enabled ? String(localized: "Acceso al iniciar cada tarea", bundle: .module) : String(localized: "Desactivado", bundle: .module))")
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                    Button(String(localized: "Conectar navegador…", bundle: .module), systemImage: "globe") { present(.browser) }
                } else {
                    Text(String(localized: "Actualiza el servicio para cargar las preferencias del navegador.", bundle: .module))
                        .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            ForEach(snapshot.servers) { server in
                JarvisSettingsCard(title: server.provider == "playwright"
                                  ? String(localized: "Navegador · perfil avanzado", bundle: .module) : server.title) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(server.statusTitle).font(.system(size: 12))
                                .foregroundStyle(JarvisTheme.inkSecondary)
                            ForEach(server.accounts, id: \.self) { account in
                                Text(account).font(.system(size: 13, weight: .medium)).textSelection(.enabled)
                            }
                            Text(String(localized: "\(server.approved_tools) herramientas revisadas", bundle: .module))
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                        }
                        Spacer()
                        Toggle(String(localized: "Activar \(server.title)", bundle: .module), isOn: Binding(get: { server.enabled }, set: { value in
                            if value { confirmation = .init(server: server, kind: .enable) }
                            else { Task { await changeEnabled(server, enabled: false) } }
                        }))
                        .toggleStyle(JarvisToggleControlStyle())
                    }
                    if server.provider == "google-workspace" {
                        Text(String(localized: "Acceso configurado: identidad, Gmail, Calendar y Drive. Jarvis mantiene sus límites de herramientas y sus aprobaciones.", bundle: .module))
                            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 8) {
                            Button(server.enabled ? String(localized: "Cambiar cuenta", bundle: .module) : String(localized: "Conectar cuenta", bundle: .module)) {
                                present(.googleAccount(server))
                            }
                            .disabled(!server.google_configured)
                            Button(String(localized: "Probar conexión", bundle: .module)) {
                                Task { await store.perform("settings.google.test", payload: ["server_id": server.id],
                                                           success: String(localized: "Cuenta de Google y conexión verificadas.", bundle: .module)) }
                            }
                            Button(String(localized: "Desconectar…", bundle: .module)) { confirmation = .init(server: server, kind: .disconnect) }
                        }
                        if !server.google_configured {
                            Text(String(localized: "Este perfil necesita un cliente OAuth configurado antes de conectar una cuenta.", bundle: .module))
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                        }
                    } else {
                        if server.provider == "playwright" {
                            Text(server.browser_mode == "extension" ? String(localized: "Sesión del navegador · pestaña elegida", bundle: .module)
                                 : server.browser_mode == "cdp" ? String(localized: "Conexión local CDP · pestaña elegida", bundle: .module)
                                 : String(localized: "Navegador separado", bundle: .module))
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                            Text((server.browser_origins ?? []).joined(separator: ", "))
                                .font(.system(size: 11)).textSelection(.enabled)
                            Text(String(localized: "Perfil MCP importado. Sus sitios y su sesión se administran mediante el perfil revisado.", bundle: .module))
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                        }
                        HStack {
                            Button(String(localized: "Actualizar estado", bundle: .module)) { Task { await store.refresh() } }
                            Spacer()
                            Button(String(localized: "Quitar integración…", bundle: .module)) { confirmation = .init(server: server, kind: .remove) }
                        }
                    }
                }
            }
            if snapshot.servers.isEmpty {
                ContentUnavailableView(String(localized: "Sin integraciones", bundle: .module), systemImage: "link",
                                       description: Text(String(localized: "Añade un perfil MCP revisado para conectar Jarvis.", bundle: .module)))
            }
        } else {
            ContentUnavailableView(String(localized: "Servicio sin conexión", bundle: .module), systemImage: "link",
                                   description: Text(String(localized: "Actualiza el estado para cargar tus integraciones.", bundle: .module)))
        }
        JarvisSettingsCard(title: String(localized: "Añadir integración", bundle: .module),
                          detail: String(localized: "Importa un perfil MCP con herramientas y archivos revisados. Se añadirá desactivado para que puedas comprobarlo antes de activarlo.", bundle: .module)) {
            Button(String(localized: "Importar perfil MCP…", bundle: .module), systemImage: "plus") { importProfile() }
        }
        .disabled(store.snapshot == nil)
        }
        .alert(confirmation?.title ?? String(localized: "Confirmar cambio", bundle: .module), isPresented: Binding(
            get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }
        ), presenting: confirmation) { action in
            Button(action.actionTitle, role: action.kind == .enable ? nil : .destructive) {
                Task {
                    switch action.kind {
                    case .enable: await changeEnabled(action.server, enabled: true)
                    case .disconnect:
                        await store.perform("settings.google.disconnect", payload: [
                            "server_id": action.server.id, "confirm": true,
                        ], success: String(localized: "Cuenta desconectada y acceso revocado en Google.", bundle: .module))
                    case .remove:
                        await store.perform("settings.mcp.remove", payload: [
                            "server_id": action.server.id, "confirm": true,
                        ], success: String(localized: "Integración retirada de Jarvis.", bundle: .module))
                    }
                }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: { action in Text(action.detail) }
        .disabled(store.busy)
    }

    private func changeEnabled(_ server: JarvisIntegrationSettings, enabled: Bool) async {
        await store.perform("settings.mcp.set_enabled", payload: [
            "server_id": server.id, "enabled": enabled, "confirm": enabled,
        ], success: enabled ? String(localized: "Integración activada. Aplica los cambios para conectar el servicio.", bundle: .module)
                            : String(localized: "Integración desactivada.", bundle: .module))
    }

    private func importProfile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.prompt = String(localized: "Importar", bundle: .module)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await store.perform("settings.mcp.import", payload: ["path": url.path],
                                   success: String(localized: "Perfil importado y desactivado. Revisa la integración antes de activarla.", bundle: .module)) }
    }
}

private struct IntegrationConfirmation {
    enum Kind { case enable, disconnect, remove }
    let server: JarvisIntegrationSettings
    let kind: Kind
    var title: String {
        switch kind {
        case .enable: String(localized: "¿Activar \(server.title)?", bundle: .module)
        case .disconnect: String(localized: "¿Desconectar Google Workspace?", bundle: .module)
        case .remove: String(localized: "¿Quitar \(server.title)?", bundle: .module)
        }
    }
    var actionTitle: String {
        switch kind {
        case .enable: String(localized: "Activar", bundle: .module)
        case .disconnect: String(localized: "Desconectar y revocar", bundle: .module)
        case .remove: String(localized: "Quitar", bundle: .module)
        }
    }
    var detail: String {
        switch kind {
        case .enable: String(localized: "Al aplicar los cambios, Jarvis iniciará esta conexión con sus \(server.approved_tools) herramientas revisadas y los permisos de su perfil.", bundle: .module)
        case .disconnect: String(localized: "Se detendrá la conexión, se revocará el acceso de esta app a la cuenta de Google y se eliminarán sus tokens locales. Podrás volver a autorizarla después.", bundle: .module)
        case .remove: String(localized: "Se detendrá la conexión y se retirará su configuración de Jarvis. Sus credenciales se conservan en el llavero; quitar el perfil no revoca permisos en el proveedor.", bundle: .module)
        }
    }
}
