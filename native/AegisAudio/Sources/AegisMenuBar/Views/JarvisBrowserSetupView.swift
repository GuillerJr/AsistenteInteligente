import SwiftUI

private enum BrowserConnectionMode: String, CaseIterable {
    case extensionSession = "extension", isolated, cdp
    var title: String {
        switch self {
        case .extensionSession: "Mi navegador"
        case .isolated: "Separado"
        case .cdp: "CDP local"
        }
    }
}

struct JarvisBrowserSetupView: View {
    let store: JarvisSettingsStore
    @Environment(\.dismiss) private var dismiss
    @State private var mode = BrowserConnectionMode.extensionSession
    @State private var tabURL = ""
    @State private var tabIndex = "0"
    @State private var endpoint = "http://127.0.0.1:9222"
    @State private var sites = ""
    @State private var remoteConsent = false
    @FocusState private var focused: Field?
    private enum Field: Hashable { case url, index, endpoint, sites }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Conectar navegador", systemImage: "globe")
                .font(.system(size: 20, weight: .semibold))
            JarvisSegmentedPicker(title: "Tipo de conexión", selection: $mode,
                options: BrowserConnectionMode.allCases.map {
                    JarvisSelectionOption(value: $0, title: $0.title)
                })
            if mode != .isolated {
                Text("Jarvis sólo actuará sobre la pestaña vinculada y rechazará acciones si su URL o contenido cambia antes de ejecutarlas.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                TextField("URL exacta de la pestaña (https://…)", text: $tabURL)
                    .focused($focused, equals: .url).jarvisInput(focused: focused == .url)
                    .accessibilityLabel("URL exacta de la pestaña")
                if mode == .cdp { HStack {
                    Text("Índice de pestaña (desde 0)").font(.system(size: 12))
                    TextField("0", text: $tabIndex)
                        .focused($focused, equals: .index).jarvisInput(focused: focused == .index)
                        .frame(width: 70).accessibilityLabel("Índice de la pestaña")
                } }
            }
            if mode == .extensionSession {
                Text("Necesita la extensión oficial de Playwright MCP para Chrome o Edge. Cuando Jarvis se conecte, la extensión te pedirá elegir la pestaña; selecciona la misma URL que indicas aquí.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                Link("Instalar la extensión oficial", destination:
                    URL(string: "https://playwright.dev/mcp/configuration/browser-extension")!)
                    .font(.system(size: 12))
            } else if mode == .cdp {
                TextField("Endpoint local de CDP", text: $endpoint)
                    .focused($focused, equals: .endpoint).jarvisInput(focused: focused == .endpoint)
                    .accessibilityLabel("Endpoint local de CDP")
                Text("Usa una instancia preparada para depuración. Este modo no habilita la depuración ni copia tu perfil personal de Chrome.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Text(mode == .isolated ? "Sitios permitidos (https://ejemplo.com)"
                 : "Otros sitios permitidos (opcional)").font(.system(size: 12, weight: .medium))
            TextField("https://ejemplo.com, https://otro.com", text: $sites, axis: .vertical)
                .lineLimit(2...3).focused($focused, equals: .sites)
                .jarvisInput(focused: focused == .sites).accessibilityLabel("Sitios permitidos")
            if let origin = selectedOrigin, mode != .isolated {
                Text("Incluido: \(origin)").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            HStack {
                Text("Analizar contenido con NVIDIA").font(.system(size: 12))
                Spacer()
                Toggle("Permitir analizar el contenido de estos sitios con NVIDIA", isOn: $remoteConsent)
                    .toggleStyle(JarvisToggleControlStyle())
            }
            Text("El contenido observado y tus instrucciones se enviarán a NVIDIA. Las pantallas de credenciales se bloquean. Cada acción se limita a la pestaña y a los sitios de este perfil.")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            HStack {
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Revisar y guardar perfil") { prepare() }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!valid || store.busy)
            }
        }
        .padding(24).frame(width: 520).jarvisSheet()
    }

    private var selectedOrigin: String? { origin(tabURL.trimmingCharacters(in: .whitespacesAndNewlines)) }
    private var allowedOrigins: [String] {
        var result = sites.components(separatedBy: CharacterSet(charactersIn: ",\n"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        if mode != .isolated, let selectedOrigin { result.append(selectedOrigin) }
        return Array(Set(result)).sorted()
    }
    private var valid: Bool {
        let origins = allowedOrigins
        guard remoteConsent, !origins.isEmpty, origins.count <= 16,
              origins.allSatisfy({ origin($0) == $0 }) else { return false }
        if mode != .isolated {
            guard selectedOrigin != nil, let index = Int(tabIndex), (0...100).contains(index) else { return false }
        }
        if mode == .cdp {
            guard let url = URLComponents(string: endpoint), url.scheme == "http",
                  url.host == "127.0.0.1", let port = url.port, (1...65535).contains(port),
                  endpoint == "http://127.0.0.1:\(port)" else { return false }
        }
        return true
    }
    private func origin(_ text: String) -> String? {
        guard let url = URLComponents(string: text), url.scheme == "https",
              let host = url.host, host.contains("."), url.user == nil, url.password == nil,
              url.port == nil || url.port == 443,
              !host.hasSuffix(".local"), !host.hasSuffix(".localhost"),
              !host.hasSuffix(".internal"), !host.hasSuffix(".lan"),
              host.range(of: "^[0-9.]+$", options: .regularExpression) == nil,
              text.rangeOfCharacter(from: .whitespacesAndNewlines) == nil,
              !text.contains("\\") else { return nil }
        return "https://\(host.lowercased())"
    }
    private func prepare() {
        guard valid else { return }
        var session: [String: Any] = ["mode": mode.rawValue, "remote_content_allowed": remoteConsent]
        if mode != .isolated {
            session["selected_tab_url"] = tabURL.trimmingCharacters(in: .whitespacesAndNewlines)
            if mode == .cdp, let index = Int(tabIndex) { session["selected_tab_index"] = index }
            else { session["selected_tab_index"] = 0 }
        }
        if mode == .cdp { session["cdp_endpoint"] = endpoint }
        let payload: [String: Any] = ["origins": allowedOrigins, "session": session, "confirm": true]
        dismiss()
        Task {
            await store.perform("settings.browser.prepare", payload: payload,
                success: "Perfil del navegador revisado y guardado desactivado. Actívalo y aplica los cambios para conectar.")
        }
    }
}
