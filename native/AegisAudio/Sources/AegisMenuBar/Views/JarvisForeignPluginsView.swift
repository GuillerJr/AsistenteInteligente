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
        JarvisSettingsCard(title: "Complementos de Codex y Claude", detail:
            "Jarvis instala sus habilidades; lo que ejecuta programas (servidores MCP, hooks) queda fuera. También puedes pedirlo en el chat: «instala el complemento de…».") {
            HStack {
                TextField("usuario/repositorio de GitHub", text: $repository).jarvisInput(focused: false)
                    .accessibilityLabel("Repositorio del marketplace")
                Button("Agregar marketplace") {
                    let value = repository
                    Task {
                        await store.foreignPlugins("plugins.foreign.marketplace.add", payload: ["repository": value],
                                                   success: "Marketplace agregado.")
                        if !store.failed { repository = "" }
                    }
                }.disabled(store.busy || repository.trimmingCharacters(in: .whitespaces).count < 3)
            }
            ForEach(store.foreignCatalogue?.marketplaces ?? []) { market in
                HStack {
                    Label(market.repository, systemImage: "shippingbox").foregroundStyle(JarvisTheme.inkSecondary)
                    Spacer()
                    Button("Quitar") {
                        Task { await store.foreignPlugins("plugins.foreign.marketplace.remove", payload: ["slug": market.slug],
                                                          success: "Marketplace quitado.") }
                    }.disabled(store.busy)
                }
            }
        }
        .task { await store.foreignPlugins() }
        let plugins = (store.foreignCatalogue?.plugins ?? []).filter {
            search.isEmpty || $0.name.localizedStandardContains(search) || $0.description.localizedStandardContains(search)
        }
        if store.foreignCatalogue != nil, plugins.isEmpty {
            ContentUnavailableView("Sin complementos", systemImage: "puzzlepiece.extension",
                                   description: Text("Agrega un marketplace o instala complementos en Codex o Claude."))
        }
        ForEach(plugins) { plugin in
            JarvisSettingsCard(title: plugin.name, detail: plugin.description) {
                HStack {
                    let app = plugin.format == "codex" ? "Codex" : "Claude"
                    Text("\(plugin.source == app ? app : "\(app) · \(plugin.source)") · \(plugin.skills.count) habilidades")
                        .foregroundStyle(JarvisTheme.inkSecondary)
                    Spacer()
                    Button(plugin.installed ? "Quitar" : "Instalar") {
                        Task {
                            await store.foreignPlugins(plugin.installed ? "plugins.foreign.remove" : "plugins.foreign.install",
                                                       payload: ["plugin_id": plugin.plugin_id],
                                                       success: plugin.installed ? "Complemento quitado." : "Complemento instalado.")
                            await store.refreshExtensions()
                        }
                    }.disabled(store.busy || !(plugin.installed || plugin.installable))
                }
                if !plugin.left_out.isEmpty {
                    Text("Queda fuera: " + plugin.left_out.joined(separator: "; "))
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                }
            }
        }
    }
}
