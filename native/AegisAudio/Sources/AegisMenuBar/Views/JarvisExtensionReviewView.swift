import AegisAudioCore
import SwiftUI

/// The settings window owns presentation; review content never interprets package code.
struct JarvisExtensionReviewView: View {
    let item: JarvisExtensionSheet
    let store: JarvisSettingsStore
    @Environment(\.dismiss) private var dismiss
    @State private var role = "planner"
    @State private var phrases = ""
    @State private var terms = ""
    @State private var tools = Set<String>()
    @State private var resourcePaths = Set<String>()
    @State private var reviewed = false
    @State private var initialized = false

    private let roles = ["planner", "code_security", "critical_reasoner", "vision", "omni"]
    private var blocked: Bool { store.busy || store.extensionsSnapshot?.busy != false }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(title).font(.system(size: 20, weight: .semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                MenuBarIconButton(symbol: "xmark", title: String(localized: "Cerrar revisión de extensión", bundle: .module)) { dismiss() }
                    .disabled(store.busy)
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) { content }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.scrollBounceBehavior(.basedOnSize)
            if store.failed, let message = store.message {
                Label(message, systemImage: "exclamationmark.circle")
                    .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            HStack {
                Button(String(localized: "Cerrar", bundle: .module)) { dismiss() }.keyboardShortcut(.cancelAction).disabled(store.busy)
                if store.busy { ProgressView().controlSize(.small) }
                Spacer()
                actions
            }
        }
        .padding(24).frame(width: 640, height: 620).jarvisSheet()
        .toggleStyle(JarvisSwitchStyle())
        .onAppear { initialize() }
    }

    private var title: String {
        switch item.content {
        case .skill(let skill, _): String(localized: "Revisar \(skill.name)", bundle: .module)
        case .importSkill(_, let preview): String(localized: "Importar \(preview.name)", bundle: .module)
        case .importPlugin(_, let preview): String(localized: "Importar \(preview.name)", bundle: .module)
        case .plugin(let plugin, _): String(localized: "Administrar \(plugin.name)", bundle: .module)
        case .confirm(let title, _, _, _, _): title
        }
    }

    @ViewBuilder private var content: some View {
        switch item.content {
        case .skill(let skill, let details):
            Text(skill.description).foregroundStyle(JarvisTheme.inkSecondary)
            Text(String(localized: "\(skill.originTitle) · \(JarvisModelTask.title(skill.role))", bundle: .module)).font(.system(size: 12))
            procedure(instructions: details.instructions, resources: details.resources)
            toolList(skill.allowed_tools)
            digest(details.content_sha256)
            if skill.builtin_public {
                Text(String(localized: "Procedimiento público integrado. Se utiliza con NVIDIA cuando la skill está activa.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            } else {
                Text(String(localized: "Autorizar permite enviar estas instrucciones y recursos a la API de NVIDIA cuando Jarvis seleccione la skill. La autorización corresponde a esta versión y deberá repetirse si cambia.", bundle: .module))
                    .font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                if !skill.sharingAuthorized {
                    Toggle(String(localized: "He revisado el contenido completo", bundle: .module), isOn: $reviewed)
                }
            }
        case .importSkill(_, let preview):
            skillImportForm(preview)
        case .importPlugin(_, let preview), .plugin(_, let preview):
            pluginInformation(preview)
            Toggle(String(localized: "He revisado las conexiones y los procedimientos", bundle: .module), isOn: $reviewed)
        case .confirm(_, let detail, _, _, _):
            Text(detail).font(.system(size: 14)).lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var actions: some View {
        switch item.content {
        case .skill(let skill, let details):
            if !skill.builtin_public {
                Button(skill.sharingAuthorized ? String(localized: "Revocar NVIDIA", bundle: .module) : String(localized: "Autorizar en NVIDIA", bundle: .module)) {
                    mutate("extensions.skill.remote", payload: [
                        "skill_id": skill.id, "content_sha256": details.content_sha256,
                        "approved": !skill.sharingAuthorized, "confirm": true,
                    ], success: skill.sharingAuthorized ? String(localized: "Autorización de NVIDIA revocada.", bundle: .module) : String(localized: "Contenido de la skill autorizado para NVIDIA.", bundle: .module))
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: !skill.sharingAuthorized))
                .disabled(blocked || skill.loaded == false || (!skill.sharingAuthorized && !reviewed))
            }
        case .importSkill(let path, let preview):
            Button(String(localized: "Importar desactivada", bundle: .module)) {
                let starters = preview.starter_tools.filter { tools.contains($0) }
                mutate("extensions.skill.import", payload: [
                    "path": path, "source_sha256": preview.source_sha256, "confirm": true,
                    "choices": ["role": role, "trigger_phrases": splitPhrases(phrases),
                                "trigger_terms": splitTerms(terms), "allowed_tools": tools.sorted(),
                                "starter_tools": starters, "reviewed_resources": resourcePaths.sorted()],
                ], success: String(localized: "Skill importada desactivada. Revisa su autorización de NVIDIA y actívala cuando corresponda.", bundle: .module))
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
            .disabled(blocked || !reviewed || !validImport)
        case .importPlugin(let path, let preview):
            Button(String(localized: "Importar desactivado", bundle: .module)) {
                mutate("extensions.plugin.import", payload: ["path": path,
                    "source_sha256": preview.source_sha256, "confirm": true],
                    success: String(localized: "Plugin importado desactivado. Revisa y activa sus conexiones para aplicarlo.", bundle: .module))
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
            .disabled(blocked || !reviewed || !preview.compatible)
        case .plugin(let plugin, let preview):
            Button(String(localized: "Retirar", bundle: .module), role: .destructive) {
                mutate("extensions.plugin.remove", payload: pluginPayload(plugin, preview),
                       success: String(localized: "Plugin retirado y archivado. Sus credenciales se conservan en el llavero.", bundle: .module))
            }.disabled(blocked || !reviewed)
            Button(plugin.enabled ? String(localized: "Desactivar", bundle: .module) : String(localized: "Activar conexiones", bundle: .module)) {
                var payload = pluginPayload(plugin, preview)
                payload["enabled"] = !plugin.enabled
                mutate("extensions.plugin.set_enabled", payload: payload,
                       success: plugin.enabled ? String(localized: "Plugin desactivado. Sus herramientas ya no están disponibles.", bundle: .module)
                        : String(localized: "Plugin activado. Aplica los cambios y revisa la autorización de sus skills para NVIDIA.", bundle: .module))
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: !plugin.enabled))
            .disabled(blocked || (!plugin.enabled && !reviewed))
        case .confirm(_, _, let method, let payload, let success):
            Button(String(localized: "Confirmar", bundle: .module)) { mutate(method, payload: payload, success: success) }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                .disabled(blocked)
        }
    }

    private var validImport: Bool {
        let parsedPhrases = splitPhrases(phrases)
        let parsedTerms = splitTerms(terms)
        return roles.contains(role) && tools.count <= 16
            && (!parsedPhrases.isEmpty || !parsedTerms.isEmpty)
            && parsedPhrases.count <= 16 && parsedPhrases.allSatisfy { (3...120).contains($0.count) }
            && parsedTerms.count <= 32 && parsedTerms.allSatisfy { !$0.contains(" ") && $0.count >= 2 }
    }

    private func skillImportForm(_ preview: JarvisSkillImportPreview) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(preview.description).foregroundStyle(JarvisTheme.inkSecondary)
            Text(String(localized: "\(preview.skill_id) · \(preview.format.uppercased())", bundle: .module)).font(.system(size: 11, design: .monospaced))
            if !preview.diagnostics.isEmpty {
                Text(preview.diagnostics.map(diagnosticTitle).joined(separator: "\n"))
                    .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
            }
            if preview.requires_disabled {
                Text(String(localized: "Esta skill requiere adaptación. No se ejecutarán scripts ni se instalarán dependencias.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
            }
            Text(String(localized: "Tipo de tarea", bundle: .module)).font(.system(size: 12, weight: .semibold))
            JarvisMenuPicker(title: String(localized: "Tipo de tarea de la skill", bundle: .module), selection: $role,
                             options: roles.map { .init(value: $0, title: JarvisModelTask.title($0)) })
                .onChange(of: role) { _, new in
                    tools.formIntersection(Set(store.extensionsSnapshot?.tool_options[new] ?? []))
                }
            TextField(String(localized: "Frases de activación, separadas por punto y coma", bundle: .module), text: $phrases)
                .jarvisInput(focused: false).accessibilityLabel(String(localized: "Frases para activar la skill", bundle: .module))
            TextField(String(localized: "Palabras clave, separadas por comas", bundle: .module), text: $terms)
                .jarvisInput(focused: false).accessibilityLabel(String(localized: "Palabras clave de la skill", bundle: .module))
            DisclosureGroup(String(localized: "Herramientas disponibles para este tipo de tarea (\(tools.count)/16)", bundle: .module)) {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(store.extensionsSnapshot?.tool_options[role] ?? [], id: \.self) { name in
                        Toggle(name, isOn: Binding(get: { tools.contains(name) }, set: { selected in
                            if selected { tools.insert(name) } else { tools.remove(name) }
                        })).disabled(!tools.contains(name) && tools.count >= 16)
                    }
                }.padding(.top, 8)
            }
            Text(String(localized: "La lista acota lo que puede proponer el modelo; cada acción conserva los permisos y las confirmaciones del broker.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
            procedure(instructions: preview.instructions, resources: preview.resources)
            if let candidates = preview.resource_candidates, !candidates.isEmpty {
                Text(String(localized: "Referencias del archivo", bundle: .module)).font(.system(size: 13, weight: .semibold))
                Text(String(localized: "Puedes importar hasta tres archivos de texto del mismo directorio. Después revisa su contenido completo antes de autorizar la skill para NVIDIA. Los enlaces externos permanecen sin abrir.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                ForEach(candidates) { candidate in
                    if candidate.supported {
                        Toggle(candidate.path, isOn: Binding(get: { resourcePaths.contains(candidate.path) }, set: { selected in
                            if selected { resourcePaths.insert(candidate.path) } else { resourcePaths.remove(candidate.path) }
                        })).disabled(!resourcePaths.contains(candidate.path) && resourcePaths.count >= 3)
                    } else {
                        Label(String(localized: "\(candidate.path) · requiere adaptación", bundle: .module), systemImage: "exclamationmark.circle")
                            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    }
                }
            }
            digest(preview.source_sha256)
            Toggle(String(localized: "He revisado el contenido y las opciones", bundle: .module), isOn: $reviewed)
        }
    }

    private func pluginInformation(_ preview: JarvisPluginImportPreview) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(preview.description).foregroundStyle(JarvisTheme.inkSecondary)
            Text(String(localized: "\(preview.plugin_id) · \(preview.version)", bundle: .module)).font(.system(size: 12, design: .monospaced))
            Text(String(localized: "Conexiones: \(preview.hosts.isEmpty ? "ninguna" : preview.hosts.joined(separator: ", "))", bundle: .module))
                .font(.system(size: 12)).textSelection(.enabled)
            ForEach(preview.tools) { tool in
                VStack(alignment: .leading, spacing: 4) {
                    Text(tool.name).font(.system(size: 12, weight: .semibold, design: .monospaced))
                    Text(String(localized: "\(tool.capability) · \(tool.risk) · \(tool.description)", bundle: .module))
                        .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(12).jarvisCard()
            }
            ForEach(preview.skills) { skill in
                VStack(alignment: .leading, spacing: 8) {
                    Text(skill.name).font(.system(size: 14, weight: .semibold))
                    procedure(instructions: skill.instructions, resources: skill.resources)
                }
            }
            digest(preview.checksum_sha256)
            Text(String(localized: "Retirar un plugin conserva una copia privada y sus credenciales. Activarlo puede requerir reinicio; sus procedimientos necesitan autorización independiente para NVIDIA.", bundle: .module))
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
        }
    }

    private func procedure(instructions: [String], resources: [String]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(String(localized: "Instrucciones y recursos", bundle: .module)).font(.system(size: 13, weight: .semibold))
            ForEach(Array((instructions + resources).enumerated()), id: \.offset) { _, text in
                Text(verbatim: text).font(.system(size: 12)).lineSpacing(4).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12).jarvisCard()
            }
        }
    }

    private func toolList(_ names: [String]) -> some View {
        Text(names.isEmpty ? String(localized: "Sin herramientas propias", bundle: .module) : String(localized: "Herramientas: \(names.joined(separator: ", "))", bundle: .module))
            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary).textSelection(.enabled)
    }

    private func digest(_ hash: String) -> some View {
        Text(String(localized: "SHA256 · \(hash)", bundle: .module)).font(.system(size: 10, design: .monospaced))
            .foregroundStyle(JarvisTheme.inkSecondary).textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func diagnosticTitle(_ code: String) -> String {
        switch code {
        case "executable_content_disabled": String(localized: "Contiene pasos de ejecución que necesitan adaptación declarativa.", bundle: .module)
        case "external_execution_metadata_ignored": String(localized: "Los permisos, scripts y dependencias del formato original no se importarán.", bundle: .module)
        case "referenced_resources_require_review": String(localized: "Las referencias adicionales requieren selección y revisión.", bundle: .module)
        default: String(localized: "El formato contiene opciones que requieren revisión antes de activarlo.", bundle: .module)
        }
    }

    private func pluginPayload(_ plugin: JarvisExtensionPlugin, _ preview: JarvisPluginImportPreview) -> [String: Any] {
        ["plugin_id": plugin.id, "checksum_sha256": preview.checksum_sha256, "confirm": true]
    }

    private func splitPhrases(_ text: String) -> [String] {
        text.components(separatedBy: ";").map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }

    private func splitTerms(_ text: String) -> [String] {
        text.components(separatedBy: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }

    private func initialize() {
        guard !initialized else { return }
        initialized = true
        store.clearMessage()
        if case .importSkill(_, let preview) = item.content {
            role = preview.role ?? "planner"
            phrases = preview.trigger_phrases.joined(separator: "; ")
            terms = preview.trigger_terms.joined(separator: ", ")
            tools = Set(preview.allowed_tools)
        }
    }

    private func mutate(_ method: String, payload: [String: Any], success: String) {
        Task {
            await store.perform(method, payload: payload, success: success)
            if !store.failed { dismiss() }
        }
    }
}
