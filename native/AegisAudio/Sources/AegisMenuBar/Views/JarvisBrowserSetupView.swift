import SwiftUI

private enum BrowserConnectionMode: String, CaseIterable {
    case chrome, extensionSession = "extension", isolated

    var title: String {
        switch self {
        case .chrome: String(localized: "Chrome directo", bundle: .module)
        case .extensionSession: String(localized: "Extensión", bundle: .module)
        case .isolated: String(localized: "Separado", bundle: .module)
        }
    }
}

struct JarvisBrowserSetupView: View {
    let store: JarvisSettingsStore
    @Environment(\.dismiss) private var dismiss
    @State private var mode: BrowserConnectionMode
    @State private var enabled: Bool

    init(store: JarvisSettingsStore) {
        self.store = store
        let preferences = store.snapshot?.browser_automation
        // Seed the form once; polling must not replace the owner's current choices.
        _mode = State(initialValue: BrowserConnectionMode(rawValue: preferences?.mode ?? "extension")
                      ?? .extensionSession)
        _enabled = State(initialValue: preferences?.enabled ?? false)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(String(localized: "Conectar navegador", bundle: .module), systemImage: "globe")
                .font(.system(size: 20, weight: .semibold))
            Toggle(String(localized: "Usar navegador en tareas", bundle: .module), isOn: $enabled)
                .font(.system(size: 12)).toggleStyle(JarvisSwitchStyle())
            JarvisSegmentedPicker(title: String(localized: "Navegador para las tareas", bundle: .module), selection: $mode,
                options: BrowserConnectionMode.allCases.map {
                    JarvisSelectionOption(value: $0, title: $0.title)
                })
            Text(String(localized: "Cada tarea te pedirá acceso cuando sea necesario. Jarvis resolverá la pestaña y limitará el acceso a esa tarea.", bundle: .module))
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if mode == .chrome {
                Text(String(localized: "Conecta al Chrome que ya tienes abierto, sin extensión ni puerto manual. En Chrome, abre chrome://inspect/#remote-debugging y activa «Allow remote debugging for this browser instance». Chrome puede pedirte autorizar la conexión.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                Text(String(localized: "La depuración remota permite controlar esa sesión de Chrome. Jarvis limita sus herramientas al sitio aprobado para cada tarea.", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if mode == .extensionSession {
                Text(String(localized: "Usa tu navegador con la extensión oficial de Playwright MCP para Chrome o Edge. La extensión te pedirá autorizar la conexión. Jarvis buscará o abrirá la pestaña de la tarea automáticamente.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Link(String(localized: "Instalar la extensión oficial", bundle: .module), destination:
                    URL(string: "https://playwright.dev/mcp/configuration/browser-extension")!)
                    .font(.system(size: 12))
            } else {
                Text(String(localized: "Jarvis abrirá un navegador separado para la tarea. Puedes usarlo sin vincular una pestaña de tu navegador habitual.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(String(localized: "El análisis del contenido con NVIDIA se aprobará al iniciar cada tarea. Las pantallas de credenciales permanecen protegidas.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button(String(localized: "Cancelar", bundle: .module)) { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(String(localized: "Guardar preferencias", bundle: .module)) {
                    guard !store.busy, store.snapshot?.browser_automation != nil else { return }
                    let selectedMode = mode.rawValue
                    let selectedEnabled = enabled
                    dismiss()
                    Task { await store.saveBrowserAutomation(enabled: selectedEnabled, mode: selectedMode) }
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                .disabled(store.busy || store.snapshot?.browser_automation == nil)
            }
        }
        .padding(24).frame(width: 520).jarvisSheet()
    }
}
