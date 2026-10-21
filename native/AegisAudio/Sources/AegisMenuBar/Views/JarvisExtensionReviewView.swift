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
                MenuBarIconButton(symbol: "xmark", title: "Cerrar revisión de extensión") { dismiss() }
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
                Button("Cerrar") { dismiss() }.keyboardShortcut(.cancelAction).disabled(store.busy)
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
        case .skill(let skill, _): "Revisar \(skill.name)"
        case .importSkill(_, let preview): "Importar \(preview.name)"
        case .importPlugin(_, let preview): "Importar \(preview.name)"
        case .plugin(let plugin, _): "Administrar \(plugin.name)"
        case .confirm(let title, _, _, _, _): title
        }
    }

    @ViewBuilder private var content: some View {
        switch item.content {
        case .skill(let skill, let details):
            Text(skill.description).foregroundStyle(JarvisTheme.inkSecondary)
            Text("\(skill.originTitle) · \(JarvisModelTask.title(skill.role))").font(.system(size: 12))
            procedure(instructions: details.instructions, resources: details.resources)
            toolList(skill.allowed_tools)
            digest(details.content_sha256)
            if skill.builtin_public {
                Text("Procedimiento público integrado. Se utiliza con NVIDIA cuando la skill está activa.")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            } else {
                Text("Autorizar permite enviar estas instrucciones y recursos a la API de NVIDIA cuando Jarvis seleccione la skill. La autorización corresponde a esta versión y deberá repetirse si cambia.")
                    .font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                if !skill.sharingAuthorized {
                    Toggle("He revisado el contenido completo", isOn: $reviewed)
                }
            }
        case .importSkill(_, let preview):
            skillImportForm(preview)
        case .importPlugin(_, let preview), .plugin(_, let preview):
            pluginInformation(preview)
            Toggle("He revisado las conexiones y los procedimientos", isOn: $reviewed)
        case .confirm(_, let detail, _, _, _):
            Text(detail).font(.system(size: 14)).lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var actions: some View {
        switch item.content {
        case .skill(let skill, let details):
            if !skill.builtin_public {
                Button(skill.sharingAuthorized ? "Revocar NVIDIA" : "Autorizar en NVIDIA") {
                    mutate("extensions.skill.remote", payload: [
                        "skill_id": skill.id, "content_sha256": details.content_sha256,
                        "approved": !skill.sharingAuthorized, "confirm": true,
                    ], success: skill.sharingAuthorized ? "Autorización de NVIDIA revocada." : "Contenido de la skill autorizado para NVIDIA.")
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: !skill.sharingAuthorized))
                .disabled(blocked || skill.loaded == false || (!skill.sharingAuthorized && !reviewed))
            }
        case .importSkill(let path, let preview):
            Button("Importar desactivada") {
                let starters = preview.starter_tools.filter { tools.contains($0) }
                mutate("extensions.skill.import", payload: [
                    "path": path, "source_sha256": preview.source_sha256, "confirm": true,
                    "choices": ["role": role, "trigger_phrases": splitPhrases(phrases),
                                "trigger_terms": splitTerms(terms), "allowed_tools": tools.sorted(),
                                "starter_tools": starters, "reviewed_resources": resourcePaths.sorted()],
                ], success: "Skill importada desactivada. Revisa su autorización de NVIDIA y actívala cuando corresponda.")
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
            .disabled(blocked || !reviewed || !validImport)
        case .importPlugin(let path, let preview):
            Button("Importar desactivado") {
                mutate("extensions.plugin.import", payload: ["path": path,
                    "source_sha256": preview.source_sha256, "confirm": true],
                    success: "Plugin importado desactivado. Revisa y activa sus conexiones para aplicarlo.")
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
            .disabled(blocked || !reviewed || !preview.compatible)
        case .plugin(let plugin, let preview):
            Button("Retirar", role: .destructive) {
                mutate("extensions.plugin.remove", payload: pluginPayload(plugin, preview),
                       success: "Plugin retirado y archivado. Sus credenciales se conservan en el llavero.")
            }.disabled(blocked || !reviewed)
            Button(plugin.enabled ? "Desactivar" : "Activar conexiones") {
                var payload = pluginPayload(plugin, preview)
                payload["enabled"] = !plugin.enabled
                mutate("extensions.plugin.set_enabled", payload: payload,
                       success: plugin.enabled ? "Plugin desactivado. Sus herramientas ya no están disponibles."
                        : "Plugin activado. Aplica los cambios y revisa la autorización de sus skills para NVIDIA.")
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: !plugin.enabled))
            .disabled(blocked || (!plugin.enabled && !reviewed))
        case .confirm(_, _, let method, let payload, let success):
            Button("Confirmar") { mutate(method, payload: payload, success: success) }
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
            Text("\(preview.skill_id) · \(preview.format.uppercased())").font(.system(size: 11, design: .monospaced))
            if !preview.diagnostics.isEmpty {
                Text(preview.diagnostics.map(diagnosticTitle).joined(separator: "\n"))
                    .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
            }
            if preview.requires_disabled {
                Text("Esta skill requiere adaptación. No se ejecutarán scripts ni se instalarán dependencias.")
                    .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
            }
            Text("Tipo de tarea").font(.system(size: 12, weight: .semibold))
            JarvisMenuPicker(title: "Tipo de tarea de la skill", selection: $role,
                             options: roles.map { .init(value: $0, title: JarvisModelTask.title($0)) })
                .onChange(of: role) { _, new in
                    tools.formIntersection(Set(store.extensionsSnapshot?.tool_options[new] ?? []))
                }
            TextField("Frases de activación, separadas por punto y coma", text: $phrases)
                .jarvisInput(focused: false).accessibilityLabel("Frases para activar la skill")
            TextField("Palabras clave, separadas por comas", text: $terms)
                .jarvisInput(focused: false).accessibilityLabel("Palabras clave de la skill")
            DisclosureGroup("Herramientas disponibles para este tipo de tarea (\(tools.count)/16)") {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(store.extensionsSnapshot?.tool_options[role] ?? [], id: \.self) { name in
                        Toggle(name, isOn: Binding(get: { tools.contains(name) }, set: { selected in
                            if selected { tools.insert(name) } else { tools.remove(name) }
                        })).disabled(!tools.contains(name) && tools.count >= 16)
                    }
                }.padding(.top, 8)
            }
            Text("La lista acota lo que puede proponer el modelo; cada acción conserva los permisos y las confirmaciones del broker.")
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
            procedure(instructions: preview.instructions, resources: preview.resources)
            if let candidates = preview.resource_candidates, !candidates.isEmpty {
                Text("Referencias del archivo").font(.system(size: 13, weight: .semibold))
                Text("Puedes importar hasta tres archivos de texto del mismo directorio. Después revisa su contenido completo antes de autorizar la skill para NVIDIA. Los enlaces externos permanecen sin abrir.")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                ForEach(candidates) { candidate in
                    if candidate.supported {
                        Toggle(candidate.path, isOn: Binding(get: { resourcePaths.contains(candidate.path) }, set: { selected in
                            if selected { resourcePaths.insert(candidate.path) } else { resourcePaths.remove(candidate.path) }
                        })).disabled(!resourcePaths.contains(candidate.path) && resourcePaths.count >= 3)
                    } else {
                        Label("\(candidate.path) · requiere adaptación", systemImage: "exclamationmark.circle")
                            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    }
                }
            }
            digest(preview.source_sha256)
            Toggle("He revisado el contenido y las opciones", isOn: $reviewed)
        }
    }

    private func pluginInformation(_ preview: JarvisPluginImportPreview) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(preview.description).foregroundStyle(JarvisTheme.inkSecondary)
            Text("\(preview.plugin_id) · \(preview.version)").font(.system(size: 12, design: .monospaced))
            Text("Conexiones: \(preview.hosts.isEmpty ? "ninguna" : preview.hosts.joined(separator: ", "))")
                .font(.system(size: 12)).textSelection(.enabled)
            ForEach(preview.tools) { tool in
                VStack(alignment: .leading, spacing: 4) {
                    Text(tool.name).font(.system(size: 12, weight: .semibold, design: .monospaced))
                    Text("\(tool.capability) · \(tool.risk) · \(tool.description)")
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
            Text("Retirar un plugin conserva una copia privada y sus credenciales. Activarlo puede requerir reinicio; sus procedimientos necesitan autorización independiente para NVIDIA.")
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
        }
    }

    private func procedure(instructions: [String], resources: [String]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Instrucciones y recursos").font(.system(size: 13, weight: .semibold))
            ForEach(Array((instructions + resources).enumerated()), id: \.offset) { _, text in
                Text(verbatim: text).font(.system(size: 12)).lineSpacing(4).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12).jarvisCard()
            }
        }
    }

    private func toolList(_ names: [String]) -> some View {
        Text(names.isEmpty ? "Sin herramientas propias" : "Herramientas: \(names.joined(separator: ", "))")
            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary).textSelection(.enabled)
    }

    private func digest(_ hash: String) -> some View {
        Text("SHA256 · \(hash)").font(.system(size: 10, design: .monospaced))
            .foregroundStyle(JarvisTheme.inkSecondary).textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func diagnosticTitle(_ code: String) -> String {
        switch code {
        case "executable_content_disabled": "Contiene pasos de ejecución que necesitan adaptación declarativa."
        case "external_execution_metadata_ignored": "Los permisos, scripts y dependencias del formato original no se importarán."
        case "referenced_resources_require_review": "Las referencias adicionales requieren selección y revisión."
        default: "El formato contiene opciones que requieren revisión antes de activarlo."
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
