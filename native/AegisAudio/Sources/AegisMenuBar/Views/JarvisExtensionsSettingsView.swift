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
        JarvisSettingsCard(title: String(localized: "Procedimientos y conexiones", bundle: .module), detail:
            String(localized: "Las skills orientan cómo resolver una tarea. Los plugins añaden herramientas declaradas y conexiones. Sus permisos siguen sujetos a las aprobaciones de Jarvis.", bundle: .module)) {
            JarvisSegmentedPicker(title: String(localized: "Tipo de extensión", bundle: .module), selection: $category,
                                  options: [.init(value: "skills", title: String(localized: "Skills", bundle: .module)),
                                            .init(value: "plugins", title: String(localized: "Plugins", bundle: .module)),
                                            .init(value: "foreign", title: String(localized: "Codex y Claude", bundle: .module))])
            HStack {
                TextField(String(localized: "Buscar por nombre o descripción", bundle: .module), text: $search).jarvisInput(focused: false)
                    .accessibilityLabel(String(localized: "Buscar extensiones", bundle: .module))
                if category != "foreign" {
                    Button(String(localized: "Importar…", bundle: .module), systemImage: "square.and.arrow.down") { chooseSource() }
                        .disabled(unavailable)
                }
            }
            if let error = store.extensionsError { Text(error).foregroundStyle(JarvisTheme.inkSecondary) }
            if store.extensionsSnapshot?.busy == true {
                Label(String(localized: "Una tarea está en curso. Los cambios estarán disponibles cuando termine.", bundle: .module),
                      systemImage: "clock").foregroundStyle(JarvisTheme.inkSecondary)
            }
            if store.extensionsSnapshot?.policy_status?.valid == false {
                Label(String(localized: "La política local de extensiones requiere reparación. Las skills están bloqueadas.", bundle: .module),
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
                Button(String(localized: "Verificar integridad", bundle: .module), systemImage: "checkmark.shield") {
                    Task {
                        await store.perform("extensions.plugin.verify", success: String(localized: "Verificación completada. Revisa el estado de cada plugin.", bundle: .module))
                    }
                }.disabled(store.busy)
                Text(String(localized: "Formato compatible: paquetes JSON declarativos de Jarvis. Las skills admiten JSON y SKILL.md con metadatos sencillos; los scripts y las dependencias requieren adaptación.", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
            }
        }
    }

    private var emptyState: some View {
        ContentUnavailableView(search.isEmpty ? String(localized: "Sin extensiones personales", bundle: .module) : String(localized: "Sin coincidencias", bundle: .module),
                               systemImage: "puzzlepiece.extension",
                               description: Text(String(localized: "Importa un archivo para revisar su contenido antes de activarlo.", bundle: .module)))
    }

    private func matches(_ name: String, _ description: String) -> Bool {
        search.isEmpty || name.localizedStandardContains(search) || description.localizedStandardContains(search)
    }

    private func skillCard(_ skill: JarvisExtensionSkill) -> some View {
        JarvisSettingsCard(title: skill.name, detail: skill.description) {
            HStack {
                Text(String(localized: "\(skill.originTitle) · \(JarvisModelTask.title(skill.role))", bundle: .module))
                    .foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Text(skill.statusTitle).foregroundStyle(skill.active && skill.remote_allowed
                    ? HUDStyle.accent(for: .success) : JarvisInterfaceStyle.secondaryText)
                    .multilineTextAlignment(.trailing)
            }
            Toggle(String(localized: "Usar esta skill", bundle: .module), isOn: Binding(get: { skill.enabled }, set: { enabled in
                present(.init(content: .confirm(
                    title: enabled ? String(localized: "Activar \(skill.name)", bundle: .module) : String(localized: "Desactivar \(skill.name)", bundle: .module),
                    detail: enabled ? String(localized: "Jarvis podrá seleccionarla en tareas compatibles. Revisa y autoriza por separado sus instrucciones para NVIDIA.", bundle: .module)
                        : String(localized: "Jarvis dejará de seleccionarla para tareas nuevas.", bundle: .module),
                    method: "extensions.skill.set_enabled", payload: skillPayload(skill, ["enabled": enabled]),
                    success: enabled ? String(localized: "Skill activada.", bundle: .module) : String(localized: "Skill desactivada.", bundle: .module))))
            })).disabled(unavailable || skill.loaded == false || skill.adaptation_required == true)
            HStack {
                Button(String(localized: "Revisar instrucciones", bundle: .module), systemImage: "doc.text.magnifyingglass") {
                    Task {
                        if let details = await store.skillDetails(skill) {
                            present(.init(content: .skill(skill, details)))
                        }
                    }
                }.disabled(store.busy || !skill.preview_available)
                Spacer()
                if skill.origin == "learned" {
                    Button(String(localized: "Retirar", bundle: .module), role: .destructive) {
                        present(.init(content: .confirm(title: String(localized: "Retirar \(skill.name)", bundle: .module),
                            detail: String(localized: "Se retirará del catálogo y se conservará una copia privada para recuperarla.", bundle: .module),
                            method: "extensions.skill.remove", payload: skillPayload(skill), success: String(localized: "Skill retirada y archivada.", bundle: .module))))
                    }.disabled(unavailable)
                }
            }
        }
    }

    private func pluginCard(_ plugin: JarvisExtensionPlugin) -> some View {
        JarvisSettingsCard(title: plugin.name, detail: plugin.description.isEmpty ? nil : plugin.description) {
            HStack {
                Text(String(localized: "\(plugin.version) · \(plugin.skills) skills · \(plugin.tools) herramientas", bundle: .module))
                    .foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Label(plugin.integrity == "intact" ? String(localized: "Integridad verificada", bundle: .module) : String(localized: "Requiere revisión", bundle: .module),
                      systemImage: plugin.integrity == "intact" ? "checkmark.shield" : "exclamationmark.shield")
                    .foregroundStyle(plugin.integrity == "intact" ? JarvisInterfaceStyle.secondaryText
                                     : HUDStyle.accent(for: .warning))
            }
            Text(plugin.restart_required ? String(localized: "Pendiente de aplicar y reiniciar", bundle: .module)
                 : plugin.active ? String(localized: "Conexiones activas", bundle: .module) : String(localized: "Desactivado", bundle: .module)).foregroundStyle(JarvisTheme.inkSecondary)
            HStack {
                Button(String(localized: "Revisar y administrar", bundle: .module), systemImage: "slider.horizontal.3") {
                    Task {
                        if let details = await store.pluginDetails(plugin) {
                            present(.init(content: .plugin(plugin, details)))
                        }
                    }
                }.disabled(store.busy || plugin.integrity != "intact")
                Spacer()
            }
            if plugin.integrity != "intact" {
                Text(String(localized: "El paquete no coincide con su sello local. Revisa el archivo original y vuelve a importarlo.", bundle: .module))
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
        panel.message = category == "plugins" ? String(localized: "Elige un paquete JSON declarativo de Jarvis.", bundle: .module) : String(localized: "Elige una skill JSON o un archivo SKILL.md.", bundle: .module)
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
