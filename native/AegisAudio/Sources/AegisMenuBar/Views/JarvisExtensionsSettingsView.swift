import AegisAudioCore
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct JarvisExtensionSheet: Identifiable {
    enum Content {
        case skill(JarvisExtensionSkill, JarvisSkillDetails)
        case importSkill(String, JarvisSkillImportPreview)
        case importPlugin(String, JarvisPluginImportPreview)
        case plugin(JarvisExtensionPlugin, JarvisPluginImportPreview)
        case confirm(title: String, detail: String, method: String, payload: [String: Any], success: String)
    }
    let id = UUID()
    let content: Content
}

struct JarvisExtensionsSettingsView: View {
    let store: JarvisSettingsStore
    let present: (JarvisExtensionSheet) -> Void
    @State private var category = "skills"
    @State private var search = ""

    private var unavailable: Bool {
        store.extensionsSnapshot == nil || store.extensionsSnapshot?.busy == true || store.busy
    }

    var body: some View {
        JarvisSettingsCard(title: "Procedimientos y conexiones", detail:
            "Las skills orientan cómo resolver una tarea. Los plugins añaden herramientas declaradas y conexiones. Sus permisos siguen sujetos a las aprobaciones de Jarvis.") {
            JarvisSegmentedPicker(title: "Tipo de extensión", selection: $category,
                                  options: [.init(value: "skills", title: "Skills"),
                                            .init(value: "plugins", title: "Plugins"),
                                            .init(value: "foreign", title: "Codex y Claude")])
            HStack {
                TextField("Buscar por nombre o descripción", text: $search).jarvisInput(focused: false)
                    .accessibilityLabel("Buscar extensiones")
                if category != "foreign" {
                    Button("Importar…", systemImage: "square.and.arrow.down") { chooseSource() }
                        .disabled(unavailable)
                }
            }
            if let error = store.extensionsError { Text(error).foregroundStyle(JarvisTheme.inkSecondary) }
            if store.extensionsSnapshot?.busy == true {
                Label("Una tarea está en curso. Los cambios estarán disponibles cuando termine.",
                      systemImage: "clock").foregroundStyle(JarvisTheme.inkSecondary)
            }
            if store.extensionsSnapshot?.policy_status?.valid == false {
                Label("La política local de extensiones requiere reparación. Las skills están bloqueadas.",
                      systemImage: "exclamationmark.shield").foregroundStyle(HUDStyle.accent(for: .warning))
            }
        }
        if category == "foreign" {
            JarvisForeignPluginsView(store: store, search: search)
        } else if let snapshot = store.extensionsSnapshot {
            if category == "skills" {
                let skills = snapshot.skills.filter { matches($0.name, $0.description) }
                if skills.isEmpty { emptyState }
                ForEach(skills) { skill in skillCard(skill) }
            } else {
                let plugins = snapshot.plugins.filter { matches($0.name, $0.description) }
                if plugins.isEmpty { emptyState }
                ForEach(plugins) { plugin in pluginCard(plugin) }
                Button("Verificar integridad", systemImage: "checkmark.shield") {
                    Task {
                        await store.perform("extensions.plugin.verify", success: "Verificación completada. Revisa el estado de cada plugin.")
                    }
                }.disabled(store.busy)
                Text("Formato compatible: paquetes JSON declarativos de Jarvis. Las skills admiten JSON y SKILL.md con metadatos sencillos; los scripts y las dependencias requieren adaptación.")
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
            }
        }
    }

    private var emptyState: some View {
        ContentUnavailableView(search.isEmpty ? "Sin extensiones personales" : "Sin coincidencias",
                               systemImage: "puzzlepiece.extension",
                               description: Text("Importa un archivo para revisar su contenido antes de activarlo."))
    }

    private func matches(_ name: String, _ description: String) -> Bool {
        search.isEmpty || name.localizedStandardContains(search) || description.localizedStandardContains(search)
    }

    private func skillCard(_ skill: JarvisExtensionSkill) -> some View {
        JarvisSettingsCard(title: skill.name, detail: skill.description) {
            HStack {
                Text("\(skill.originTitle) · \(JarvisModelTask.title(skill.role))")
                    .foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Text(skill.statusTitle).foregroundStyle(skill.active && skill.remote_allowed
                    ? HUDStyle.accent(for: .success) : JarvisInterfaceStyle.secondaryText)
                    .multilineTextAlignment(.trailing)
            }
            Toggle("Usar esta skill", isOn: Binding(get: { skill.enabled }, set: { enabled in
                present(.init(content: .confirm(
                    title: enabled ? "Activar \(skill.name)" : "Desactivar \(skill.name)",
                    detail: enabled ? "Jarvis podrá seleccionarla en tareas compatibles. Revisa y autoriza por separado sus instrucciones para NVIDIA."
                        : "Jarvis dejará de seleccionarla para tareas nuevas.",
                    method: "extensions.skill.set_enabled", payload: skillPayload(skill, ["enabled": enabled]),
                    success: enabled ? "Skill activada." : "Skill desactivada.")))
            })).disabled(unavailable || skill.loaded == false || skill.adaptation_required == true)
            HStack {
                Button("Revisar instrucciones", systemImage: "doc.text.magnifyingglass") {
                    Task {
                        if let details = await store.skillDetails(skill) {
                            present(.init(content: .skill(skill, details)))
                        }
                    }
                }.disabled(store.busy || !skill.preview_available)
                Spacer()
                if skill.origin == "learned" {
                    Button("Retirar", role: .destructive) {
                        present(.init(content: .confirm(title: "Retirar \(skill.name)",
                            detail: "Se retirará del catálogo y se conservará una copia privada para recuperarla.",
                            method: "extensions.skill.remove", payload: skillPayload(skill), success: "Skill retirada y archivada.")))
                    }.disabled(unavailable)
                }
            }
        }
    }

    private func pluginCard(_ plugin: JarvisExtensionPlugin) -> some View {
        JarvisSettingsCard(title: plugin.name, detail: plugin.description.isEmpty ? nil : plugin.description) {
            HStack {
                Text("\(plugin.version) · \(plugin.skills) skills · \(plugin.tools) herramientas")
                    .foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Label(plugin.integrity == "intact" ? "Integridad verificada" : "Requiere revisión",
                      systemImage: plugin.integrity == "intact" ? "checkmark.shield" : "exclamationmark.shield")
                    .foregroundStyle(plugin.integrity == "intact" ? JarvisInterfaceStyle.secondaryText
                                     : HUDStyle.accent(for: .warning))
            }
            Text(plugin.restart_required ? "Pendiente de aplicar y reiniciar"
                 : plugin.active ? "Conexiones activas" : "Desactivado").foregroundStyle(JarvisTheme.inkSecondary)
            HStack {
                Button("Revisar y administrar", systemImage: "slider.horizontal.3") {
                    Task {
                        if let details = await store.pluginDetails(plugin) {
                            present(.init(content: .plugin(plugin, details)))
                        }
                    }
                }.disabled(store.busy || plugin.integrity != "intact")
                Spacer()
            }
            if plugin.integrity != "intact" {
                Text("El paquete no coincide con su sello local. Revisa el archivo original y vuelve a importarlo.")
                    .foregroundStyle(JarvisTheme.inkSecondary)
            }
        }
    }

    private func skillPayload(_ skill: JarvisExtensionSkill, _ extra: [String: Any] = [:]) -> [String: Any] {
        ["skill_id": skill.id, "content_sha256": skill.content_sha256, "confirm": true]
            .merging(extra) { _, new in new }
    }

    private func chooseSource() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = category == "plugins" ? [.json] : [.json, .plainText, UTType(filenameExtension: "md") ?? .plainText]
        panel.message = category == "plugins" ? "Elige un paquete JSON declarativo de Jarvis." : "Elige una skill JSON o un archivo SKILL.md."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let importingPlugin = category == "plugins"
        Task {
            if importingPlugin, let preview = await store.pluginPreview(path: url.path) {
                present(.init(content: .importPlugin(url.path, preview)))
            } else if !importingPlugin, let preview = await store.skillPreview(path: url.path) {
                present(.init(content: .importSkill(url.path, preview)))
            }
        }
    }
}
