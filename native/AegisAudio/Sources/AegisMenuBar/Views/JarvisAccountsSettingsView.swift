import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct JarvisAccountsSettingsView: View {
    let store: JarvisSettingsStore
    @State private var googleTarget: JarvisIntegrationSettings?
    @State private var showGoogleAuthorization = false
    @State private var email = ""
    @State private var confirmation: IntegrationConfirmation?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
        if let snapshot = store.snapshot {
            ForEach(snapshot.servers) { server in
                JarvisSettingsCard(title: server.title) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(server.statusTitle).font(.system(size: 12))
                                .foregroundStyle(.white.opacity(0.7))
                            ForEach(server.accounts, id: \.self) { account in
                                Text(account).font(.system(size: 13, weight: .medium)).textSelection(.enabled)
                            }
                            Text("\(server.approved_tools) herramientas revisadas")
                                .font(.system(size: 11)).foregroundStyle(.white.opacity(0.5))
                        }
                        Spacer()
                        Toggle("Activar \(server.title)", isOn: Binding(get: { server.enabled }, set: { value in
                            if value { confirmation = .init(server: server, kind: .enable) }
                            else { Task { await changeEnabled(server, enabled: false) } }
                        }))
                        .labelsHidden().toggleStyle(.switch).controlSize(.small)
                    }
                    if server.provider == "google-workspace" {
                        Text("Acceso configurado: identidad, Gmail, Calendar y Drive. Jarvis mantiene sus límites de herramientas y sus aprobaciones.")
                            .font(.system(size: 11)).foregroundStyle(.white.opacity(0.55))
                            .fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 8) {
                            Button(server.enabled ? "Cambiar cuenta" : "Conectar cuenta") {
                                email = server.accounts.first ?? ""
                                googleTarget = server
                                showGoogleAuthorization = true
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
                                .font(.system(size: 11)).foregroundStyle(.white.opacity(0.6))
                        }
                    } else {
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
        }
        .disabled(store.snapshot == nil)
        }
        .sheet(isPresented: $showGoogleAuthorization) {
            if let server = googleTarget {
            VStack(alignment: .leading, spacing: 18) {
                Label("Cuenta de Google", systemImage: "person.crop.circle")
                    .font(.system(size: 20, weight: .semibold))
                Text("Escribe la cuenta que quieres usar. Google solicitará autorización en tu navegador; Jarvis verificará que el correo coincida antes de guardar el cambio.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                TextField("Correo de Google", text: $email).textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Correo de la cuenta de Google")
                Text("La conexión actual se pausará durante la autorización. Después podrás aplicar los cambios para reiniciar el servicio.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                HStack {
                    Button("Cancelar") { showGoogleAuthorization = false }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button("Autorizar en Google") {
                        let account = email.trimmingCharacters(in: .whitespacesAndNewlines)
                        showGoogleAuthorization = false
                        Task { await store.perform("settings.google.connect", payload: [
                            "server_id": server.id, "email": account, "confirm": true,
                        ], success: "Cuenta conectada. Aplica los cambios para reiniciar el servicio.") }
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!validEmail)
                }
            }
            .padding(24).frame(width: 480).jarvisSheet()
            }
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

    private var validEmail: Bool {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
            .range(of: "^[A-Za-z0-9][A-Za-z0-9._+%-]*@[A-Za-z0-9.-]+\\.[A-Za-z]{2,63}$", options: .regularExpression) != nil
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
