import AppKit
import DesktopCore
import SwiftUI

struct MCPConfigurationView: View {
    @Bindable var mcp: MCPStore
    let canManage: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var serverID = ""
    @State private var executable = ""
    @State private var argumentsJSON = "[]"
    @State private var protocolVersion = ""
    @State private var errorMessage: String?
    @State private var transport = MCPTransport.stdio
    @State private var endpoint = ""
    @State private var allowLoopback = false
    @State private var useOAuth = false
    @State private var oauthResource = ""
    @State private var oauthClient = ""
    @State private var oauthScopes = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Configurar servidor MCP").font(.title2.weight(.semibold))
            Text("Esta configuración se guarda sin ejecutar el programa. No incluyas claves ni contraseñas en los argumentos.")
                .foregroundStyle(.secondary)
            TextField("Identificador, por ejemplo: herramientas_locales", text: $serverID)
            Picker("Transporte", selection: $transport) {
                ForEach(MCPTransport.allCases) { Text($0.title).tag($0) }
            }
            if transport == .stdio {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Ejecutable").font(.headline)
                    Text(executable.isEmpty ? "Elige el programa local que quieres usar" : executable)
                        .textSelection(.enabled).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Elegir programa…") { chooseExecutable() }
            }
            Text("Argumentos como array JSON").font(.headline)
            TextEditor(text: $argumentsJSON)
                .font(.system(.body, design: .monospaced))
                .frame(height: 110).border(.quaternary)
                .accessibilityLabel("Argumentos del servidor en JSON")
            Text("Cada elemento es un argumento exacto. Ejemplo: [\"--modo\", \"local\"]. Se ejecuta directamente, sin interpretar una línea de shell.")
                .font(.caption).foregroundStyle(.secondary)
            } else {
                TextField("Endpoint HTTPS del servidor", text: $endpoint)
                Toggle("Permitir HTTP para este destino de loopback", isOn: $allowLoopback)
                Text("La excepción sólo admite una dirección IP de loopback explícita; no habilita otras redes. No incluyas tokens en la dirección.")
                    .font(.caption).foregroundStyle(.secondary)
                Toggle("Configurar OAuth con un cliente público", isOn: $useOAuth)
                if useOAuth {
                    TextField("Recurso OAuth HTTPS", text: $oauthResource)
                    TextField("Identificador público de cliente", text: $oauthClient)
                    TextField("Scopes separados por espacios", text: $oauthScopes)
                    Text("Estos campos son metadatos públicos. El acceso se autoriza después en el navegador; no pegues secretos ni tokens.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Picker("Versión del protocolo", selection: $protocolVersion) {
                Text("Selecciona una versión").tag("")
                ForEach(mcp.supportedProtocols) { version in Text(version.rawValue).tag(version.rawValue) }
            }
            Text("Jarvis no cambia de protocolo automáticamente si la conexión falla.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = errorMessage ?? mcp.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Guardar configuración") { Task { await save() } }
                    .disabled(!canManage || mcp.isChanging || serverID.isEmpty || (transport == .stdio ? executable.isEmpty : endpoint.isEmpty) || protocolVersion.isEmpty)
            }
        }.padding(24).frame(width: 620)
    }

    private func chooseExecutable() {
        let panel = NSOpenPanel()
        panel.title = "Elegir ejecutable del servidor MCP"
        panel.message = "Selecciona el programa local que deseas ejecutar. Guardarlo aún no lo inicia."
        panel.prompt = "Usar este programa"
        panel.canChooseFiles = true; panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false; panel.resolvesAliases = false
        guard panel.runModal() == .OK, let selected = panel.url else { return }
        let path = selected.resolvingSymlinksInPath().path
        guard (try? URL(fileURLWithPath: path).resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
              FileManager.default.isExecutableFile(atPath: path) else {
            errorMessage = "El archivo elegido no tiene permiso de ejecución. Elige un programa ejecutable."
            return
        }
        executable = path; errorMessage = nil
    }

    private func save() async {
        guard canManage, let version = MCPProtocolVersion(rawValue: protocolVersion),
              mcp.supportedProtocols.contains(version) else { return }
        do {
            let configuration: MCPConfiguration
            if transport == .stdio {
                configuration = try MCPConfiguration(serverID: serverID, executable: executable, argumentsJSON: argumentsJSON, protocolVersion: version)
            } else {
                let oauth = useOAuth ? try MCPOAuthConfiguration(resource: oauthResource, clientID: oauthClient,
                    scopes: oauthScopes.split(whereSeparator: \.isWhitespace).map(String.init), allowLoopback: allowLoopback) : nil
                configuration = try MCPConfiguration(serverID: serverID, endpoint: endpoint, transport: transport,
                    protocolVersion: version, oauth: oauth, allowLoopback: allowLoopback)
            }
            errorMessage = nil
            if await mcp.add(configuration) { dismiss() }
        } catch { errorMessage = MCPPresentation.errorMessage(error) }
    }
}
