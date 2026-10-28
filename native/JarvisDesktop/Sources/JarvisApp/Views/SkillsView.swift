import AppKit
import DesktopCore
import SwiftUI

struct SkillsView: View {
    @Bindable var store: ChatStore
    @State private var importReview: SkillImportReviewSnapshot?
    @State private var editing: SkillDescriptor?
    @State private var details: SkillDescriptor?

    var body: some View {
        @Bindable var management = store.management
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Skills locales").font(.title2.weight(.semibold))
                Picker("Agente", selection: $management.selectedAgentID) {
                    ForEach(management.agents) { agent in Text(agent.name).tag(agent.id) }
                }.disabled(management.isChanging)
                Text("Importa una carpeta elegida por ti con SKILL.md. Se copia una versión del texto; no se ejecutan scripts ni se abren sus recursos.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Importar carpeta…") { chooseDirectory() }.disabled(!store.canChangeSession)
                    Button("Importar con recursos…") { chooseDirectory(withResources: true) }.disabled(!store.canChangeSession)
                    Button("Actualizar") { Task { await management.loadSkills() } }
                }
                ForEach(management.skills) { skill in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(skill.name).font(.headline)
                        Text(skill.description)
                        Text("\(skill.enabled ? "Habilitada" : "Deshabilitada") · versión \(skill.revision)")
                            .font(.caption).foregroundStyle(.secondary)
                        DisclosureGroup("Procedencia y recursos declarados") {
                            Text(skill.sourcePath).textSelection(.enabled)
                            Text("SHA-256: \(skill.hash)").font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
                            Text(skill.resources.isEmpty ? "Sin recursos declarados." : skill.resources.joined(separator: "\n"))
                                .font(.caption).textSelection(.enabled)
                            Text("La procedencia externa no está verificada. Los recursos son metadatos; no se han leído.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        HStack {
                            Button(skill.enabled ? "Deshabilitar…" : "Habilitar…") { Task { await management.enableSkill(skill) } }
                            Button("Editar…") { editing = skill }
                            Button("Usar, recursos y versiones…") { details = skill }
                            Button("Quitar…", role: .destructive) { Task { await management.removeSkill(skill) } }
                        }.disabled(!store.canChangeSession)
                    }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
                }
                if management.skills.isEmpty { Text("No hay skills importadas para este agente.").foregroundStyle(.secondary) }
                if let error = management.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                if let receipt = management.lastMutationReceipt {
                    DisclosureGroup("Recibo de la última operación") { Text(ManagementValidation.json(receipt)).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                    if receipt["cleanup_confirmed"].bool == false && management.extensionCleanupReceipt?["cleanup_confirmed"].bool != true {
                        Button("Completar cierre…") { Task { await management.completeExtensionCleanup() } }
                            .disabled(management.isChanging || store.activeRunID != nil || store.busy)
                    }
                    if let cleanup = management.extensionCleanupReceipt { Text("Recibo de cierre: " + ManagementValidation.json(cleanup)).font(.caption).textSelection(.enabled) }
                }
            }.padding(20)
        }
        .task { await management.loadSkills() }
        .onChange(of: management.selectedAgentID) { _, _ in management.skills = []; Task { await management.loadSkills() } }
        .sheet(item: $importReview) { value in SkillImportReview(selection: value, store: store) }
        .sheet(item: $editing) { value in SkillEditorView(skill: value, store: store) }
        .sheet(item: $details) { value in SkillDetailsView(skill: value, store: store) }
    }

    private func chooseDirectory(withResources: Bool = false) {
        let agentID = store.management.selectedAgentID
        let panel = NSOpenPanel()
        panel.directoryURL = store.access.workspacePath.map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.temporaryDirectory
        panel.title = "Elegir carpeta de skill"
        panel.message = withResources ? "Se leerá SKILL.md y únicamente sus recursos declarados de esta carpeta. No se ejecutará código."
            : "Se leerá únicamente SKILL.md de la carpeta seleccionada. Sus recursos no se abren ni ejecutan."
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.resolvesAliases = false; panel.prompt = "Revisar SKILL.md"
        guard panel.runModal() == .OK, let path = panel.url?.path else { return }
        do {
            let physical = try ManagementValidation.physicalPath(path)
            Task {
                if let inspected = await store.management.inspectSkill(path: physical) {
                    let selectedLibrary: SkillLibraryPreview?
                    if withResources {
                        guard let result = await store.management.inspectLibrary(path: physical), result.skillHash == inspected.hash else { return }
                        selectedLibrary = result
                    } else { selectedLibrary = nil }
                    do {
                        importReview = try SkillImportReviewSnapshot(preview: inspected,
                            library: selectedLibrary, agentID: agentID,
                            mode: withResources ? .withResources : .manifestOnly)
                    } catch {
                        store.management.errorMessage = "La revisión de la skill no coincide con sus recursos. Vuelve a elegir la carpeta."
                    }
                }
            }
        } catch { store.management.errorMessage = "No se pudo resolver la carpeta seleccionada." }
    }
}

private struct SkillImportReview: View {
    let selection: SkillImportReviewSnapshot
    private var preview: SkillPreviewSnapshot { selection.preview }
    private var library: SkillLibraryPreview? { selection.library }
    private var agentID: String { selection.agentID }
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Revisar skill: \(preview.name)").font(.title2.weight(.semibold))
            Text("Agente: \(agentID)").font(.caption)
            Text(preview.sourcePath).font(.caption).textSelection(.enabled)
            Text("SHA-256: \(preview.hash)").font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
            if let library {
                Text("Biblioteca: \(library.files.count) recursos · SHA-256 \(library.libraryHash)").font(.caption).textSelection(.enabled)
                ForEach(library.files) { file in Text("\(file.path) · \(file.bytes) bytes · \(file.hash)").font(.caption2).textSelection(.enabled) }
            }
            ScrollView { Text(preview.text).font(.system(.body, design: .monospaced)).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }
                .frame(height: 300).border(.quaternary)
            Text("La copia quedará deshabilitada. Habilitarla requerirá otra decisión tuya; ninguna instrucción de esta skill concede permisos.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = store.management.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Importar esta versión…") {
                    Task {
                        let success: Bool
                        switch selection.mode {
                        case .withResources:
                            guard let library else { return }
                            success = await store.management.importLibrary(preview, library: library, agent: agentID)
                        case .manifestOnly:
                            success = await store.management.importSkill(preview, agentID: agentID)
                        }
                        if success { dismiss() }
                    }
                }
                    .disabled(!store.canChangeSession)
            }
        }.padding(24).frame(width: 680)
    }
}

private struct SkillEditorView: View {
    let skill: SkillDescriptor
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var loaded = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Editar skill «\(skill.name)»").font(.title2.weight(.semibold))
            Text("Agente: \(skill.agentID) · versión \(skill.revision)").font(.caption)
            TextEditor(text: $text).font(.system(.body, design: .monospaced)).frame(height: 320).border(.quaternary)
                .disabled(!loaded).accessibilityLabel("Manifiesto SKILL.md completo")
            Text("Conserva el frontmatter name y description. resources es una lista JSON de rutas relativas declarativas.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = store.management.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Revisar y guardar…") { Task { if await store.management.saveSkill(skill, text: text) { dismiss() } } }
                    .disabled(!loaded || !store.canChangeSession || !ManagementValidation.text(text, limit: 32768))
            }
        }.padding(24).frame(width: 680)
        .task { if let content = await store.management.skillText(skill) { text = content; loaded = true } }
    }
}
