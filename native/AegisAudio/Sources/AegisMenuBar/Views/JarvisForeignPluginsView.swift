import SwiftUI

struct JarvisForeignPlugins: Decodable {
    struct Plugin: Decodable, Identifiable {
        struct Skill: Decodable { let name: String }
        let plugin_id: String
        let name: String
        let description: String
        let format: String
        let source: String
        let installed: Bool
        let installable: Bool
        let skills: [Skill]
        let left_out: [String]
        var id: String { plugin_id }
    }
    struct Marketplace: Decodable, Identifiable {
        let slug: String
        let repository: String
        var id: String { slug }
    }
    let plugins: [Plugin]
    let marketplaces: [Marketplace]
    let error: String?
}

/// Codex and Claude plugins: Jarvis brings over their skills and names what stays out.
struct JarvisForeignPluginsView: View {
    let store: JarvisSettingsStore
    let search: String
    @State private var repository = ""

    var body: some View {
        JarvisSettingsCard(title: String(localized: "Complementos de Codex y Claude", bundle: .module), detail:
            String(localized: "Jarvis instala sus habilidades; lo que ejecuta programas (servidores MCP, hooks) queda fuera. También puedes pedirlo en el chat: «instala el complemento de…».", bundle: .module)) {
            HStack {
                TextField(String(localized: "usuario/repositorio de GitHub", bundle: .module), text: $repository).jarvisInput(focused: false)
                    .accessibilityLabel(String(localized: "Repositorio del marketplace", bundle: .module))
                Button(String(localized: "Agregar marketplace", bundle: .module)) {
                    let value = repository
                    Task {
                        await store.foreignPlugins("plugins.foreign.marketplace.add", payload: ["repository": value],
                                                   success: String(localized: "Marketplace agregado.", bundle: .module))
                        if !store.failed { repository = "" }
                    }
                }.disabled(store.busy || repository.trimmingCharacters(in: .whitespaces).count < 3)
            }
            ForEach(store.foreignCatalogue?.marketplaces ?? []) { market in
                HStack {
                    Label(market.repository, systemImage: "shippingbox").foregroundStyle(JarvisTheme.inkSecondary)
                    Spacer()
                    Button(String(localized: "Quitar", bundle: .module)) {
                        Task { await store.foreignPlugins("plugins.foreign.marketplace.remove", payload: ["slug": market.slug],
                                                          success: String(localized: "Marketplace quitado.", bundle: .module)) }
                    }.disabled(store.busy)
                }
            }
        }
        .task { await store.foreignPlugins() }
        let plugins = (store.foreignCatalogue?.plugins ?? []).filter {
            search.isEmpty || $0.name.localizedStandardContains(search) || $0.description.localizedStandardContains(search)
        }
        if store.foreignCatalogue != nil, plugins.isEmpty {
            ContentUnavailableView(String(localized: "Sin complementos", bundle: .module), systemImage: "puzzlepiece.extension",
                                   description: Text(String(localized: "Agrega un marketplace o instala complementos en Codex o Claude.", bundle: .module)))
        }
        ForEach(plugins) { plugin in
            JarvisSettingsCard(title: plugin.name, detail: plugin.description) {
                HStack {
                    let app = plugin.format == "codex" ? String(localized: "Codex", bundle: .module) : String(localized: "Claude", bundle: .module)
                    Text(String(localized: "\(plugin.source == app ? app : "\(app) · \(plugin.source)") · \(plugin.skills.count) habilidades", bundle: .module))
                        .foregroundStyle(JarvisTheme.inkSecondary)
                    Spacer()
                    Button(plugin.installed ? String(localized: "Quitar", bundle: .module) : String(localized: "Instalar", bundle: .module)) {
                        Task {
                            await store.foreignPlugins(plugin.installed ? "plugins.foreign.remove" : "plugins.foreign.install",
                                                       payload: ["plugin_id": plugin.plugin_id],
                                                       success: plugin.installed ? String(localized: "Complemento quitado.", bundle: .module) : String(localized: "Complemento instalado.", bundle: .module))
                            await store.refreshExtensions()
                        }
                    }.disabled(store.busy || !(plugin.installed || plugin.installable))
                }
                if !plugin.left_out.isEmpty {
                    Text(String(localized: "Queda fuera: ", bundle: .module) + plugin.left_out.joined(separator: "; "))
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                }
            }
        }
    }
}
