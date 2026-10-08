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
            ForEach(snapshot.servers) { server in
                JarvisSettingsCard(title: server.title) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(server.statusTitle).font(.system(size: 12))
                                .foregroundStyle(Color.primary.opacity(0.7))
                            ForEach(server.accounts, id: \.self) { account in
                                Text(account).font(.system(size: 13, weight: .medium)).textSelection(.enabled)
                            }
                            Text("\(server.approved_tools) herramientas revisadas")
                                .font(.system(size: 11)).foregroundStyle(Color.primary.opacity(0.5))
                        }
                        Spacer()
                        Toggle("Activar \(server.title)", isOn: Binding(get: { server.enabled }, set: { value in
                            if value { confirmation = .init(server: server, kind: .enable) }
                            else { Task { await changeEnabled(server, enabled: false) } }
                        }))
                        .toggleStyle(JarvisToggleControlStyle())
                    }
                    if server.provider == "google-workspace" {
                        Text("Acceso configurado: identidad, Gmail, Calendar y Drive. Jarvis mantiene sus límites de herramientas y sus aprobaciones.")
                            .font(.system(size: 11)).foregroundStyle(Color.primary.opacity(0.55))
                            .fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 8) {
                            Button(server.enabled ? "Cambiar cuenta" : "Conectar cuenta") {
                                present(.googleAccount(server))
                            }
                            .disabled(!server.google_configured)
                            Button("Probar conexión") {
                                Task { await store.perform("settings.google.test", payload: ["server_id": server.id],
                                                           success: "Cuenta de Google y conexión verificadas.") }
                            }
                            Button("Desconectar…") { confirmation = .init(server: server, kind: .disconnect) }
                        }
                        if !server.google_configured {
                            Text("Este perfil necesita un cliente OAuth configurado antes de conectar una cuenta.")
                                .font(.system(size: 11)).foregroundStyle(Color.primary.opacity(0.6))
                        }
                    } else {
                        if server.provider == "playwright" {
                            Text(server.browser_mode == "extension" ? "Sesión del navegador · pestaña elegida"
                                 : server.browser_mode == "cdp" ? "Conexión local CDP · pestaña elegida"
                                 : "Navegador separado")
                                .font(.system(size: 11)).foregroundStyle(.secondary)
                            Text((server.browser_origins ?? []).joined(separator: ", "))
                                .font(.system(size: 11)).textSelection(.enabled)
                            Button("Configurar navegador…") { present(.browser) }
                        }
                        HStack {
                            Button("Actualizar estado") { Task { await store.refresh() } }
                            Spacer()
                            Button("Quitar integración…") { confirmation = .init(server: server, kind: .remove) }
                        }
                    }
                }
            }
            if snapshot.servers.isEmpty {
                ContentUnavailableView("Sin integraciones", systemImage: "link",
                                       description: Text("Añade un perfil MCP revisado para conectar Jarvis."))
            }
        } else {
            ContentUnavailableView("Servicio sin conexión", systemImage: "link",
                                   description: Text("Actualiza el estado para cargar tus integraciones."))
        }
        JarvisSettingsCard(title: "Añadir integración",
                          detail: "Importa un perfil MCP con herramientas y archivos revisados. Se añadirá desactivado para que puedas comprobarlo antes de activarlo.") {
            Button("Importar perfil MCP…", systemImage: "plus") { importProfile() }
            Button("Conectar navegador…", systemImage: "globe") { present(.browser) }
        }
        .disabled(store.snapshot == nil)
        }
        .alert(confirmation?.title ?? "Confirmar cambio", isPresented: Binding(
            get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }
        ), presenting: confirmation) { action in
            Button(action.actionTitle, role: action.kind == .enable ? nil : .destructive) {
                Task {
                    switch action.kind {
                    case .enable: await changeEnabled(action.server, enabled: true)
                    case .disconnect:
                        await store.perform("settings.google.disconnect", payload: [
                            "server_id": action.server.id, "confirm": true,
                        ], success: "Cuenta desconectada y acceso revocado en Google.")
                    case .remove:
                        await store.perform("settings.mcp.remove", payload: [
                            "server_id": action.server.id, "confirm": true,
                        ], success: "Integración retirada de Jarvis.")
                    }
                }
            }
            Button("Cancelar", role: .cancel) {}
        } message: { action in Text(action.detail) }
        .disabled(store.busy)
    }

    private func changeEnabled(_ server: JarvisIntegrationSettings, enabled: Bool) async {
        await store.perform("settings.mcp.set_enabled", payload: [
            "server_id": server.id, "enabled": enabled, "confirm": enabled,
        ], success: enabled ? "Integración activada. Aplica los cambios para conectar el servicio."
                            : "Integración desactivada.")
    }

    private func importProfile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.prompt = "Importar"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await store.perform("settings.mcp.import", payload: ["path": url.path],
                                   success: "Perfil importado y desactivado. Revisa la integración antes de activarla.") }
    }
}

private struct IntegrationConfirmation {
    enum Kind { case enable, disconnect, remove }
    let server: JarvisIntegrationSettings
    let kind: Kind
    var title: String {
        switch kind {
        case .enable: "¿Activar \(server.title)?"
        case .disconnect: "¿Desconectar Google Workspace?"
        case .remove: "¿Quitar \(server.title)?"
        }
    }
    var actionTitle: String {
        switch kind {
        case .enable: "Activar"
        case .disconnect: "Desconectar y revocar"
        case .remove: "Quitar"
        }
    }
    var detail: String {
        switch kind {
        case .enable: "Al aplicar los cambios, Jarvis iniciará esta conexión con sus \(server.approved_tools) herramientas revisadas y los permisos de su perfil."
        case .disconnect: "Se detendrá la conexión, se revocará el acceso de esta app a la cuenta de Google y se eliminarán sus tokens locales. Podrás volver a autorizarla después."
        case .remove: "Se detendrá la conexión y se retirará su configuración de Jarvis. Sus credenciales se conservan en el llavero; quitar el perfil no revoca permisos en el proveedor."
        }
    }
}
