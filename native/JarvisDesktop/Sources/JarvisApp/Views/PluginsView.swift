import AppKit
import DesktopCore
import SwiftUI

struct PluginsView: View {
    @Bindable var store: ChatStore
    @State private var preview: PluginPackagePreview?
    @State private var selectedPath = ""
    @State private var updating: PluginDescriptor?
    var body: some View {
        @Bindable var plugins = store.plugins
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Plugins de Jarvis").font(.title2.weight(.semibold))
                Picker("Agente", selection: Binding(get: { plugins.selectedAgentID }, set: { plugins.selectAgent($0); Task { await plugins.refresh() } })) {
                    ForEach(store.management.agents) { Text($0.name).tag($0.id) }
                }.disabled(plugins.isChanging)
                Text("Instala archivos ZIP o tar elegidos por ti. Se conservan sus archivos y versiones; el contenido no está verificado y no se ejecuta código del paquete.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Instalar paquete…") { choosePackage(nil) }.disabled(!store.canChangeSession || plugins.isChanging)
                    Button("Actualizar lista") { Task { await plugins.refresh() } }.disabled(plugins.isChanging || plugins.isLoading)
                    if plugins.isChanging || plugins.isLoading { ProgressView().controlSize(.small) }
                }
                if let error = plugins.errorMessage { Text(error).foregroundStyle(.red) }
                if let receipt = plugins.lastReceipt {
                    DisclosureGroup("Recibo de la última operación") { Text(ManagementValidation.json(receipt)).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                    if receipt["cleanup_confirmed"].bool == false && plugins.cleanupReceipt?["cleanup_confirmed"].bool != true {
                        Button("Completar cierre…") { Task { await plugins.completeCleanup() } }
                            .disabled(plugins.isChanging || store.activeRunID != nil || store.busy)
                    }
                    if let cleanup = plugins.cleanupReceipt { Text("Recibo de cierre: " + ManagementValidation.json(cleanup)).font(.caption).textSelection(.enabled) }
                }
                ForEach(plugins.plugins) { plugin in
                    PluginDetailsView(plugin: plugin, store: plugins, canManage: store.canChangeSession,
                        scope: store.selected?.scope, selectedAgent: store.selected?.agentID,
                        onUpdate: { choosePackage(plugin) }, onDraft: { addition in
                            if store.draft.utf8.count + addition.utf8.count + 2 <= 32000 { store.draft += "\n\n" + addition }
                            else { plugins.errorMessage = "El contenido completo supera el límite del borrador; no se añadió ni truncó." }
                        })
                }
                if plugins.plugins.isEmpty { Text("No hay plugins instalados para este agente.").foregroundStyle(.secondary) }
                if let resource = plugins.resource {
                    Divider()
                    Text("Recurso externo: \(resource.path)").font(.headline)
                    Text("\(resource.data.count) bytes · SHA-256 \(resource.hash)").font(.caption).textSelection(.enabled)
                    if let text = resource.text { Text(text).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                    else { Text("Contenido binario verificado por hash; no se ejecuta ni abre automáticamente.").font(.caption) }
                }
            }.padding(20)
        }
        .task { await store.plugins.refresh() }
        .sheet(item: $preview) { package in
            VStack(alignment: .leading, spacing: 14) {
                Text("Revisar \(package.name) · \(package.version)").font(.headline)
                ScrollView { Text(ManagementValidation.json(package.value)).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                    .frame(height: 340)
                Text("Se comprobará otra vez el archivo seleccionado antes de autorizar. La extensión quedará deshabilitada.").font(.caption)
                HStack {
                    Button("Cancelar") { preview = nil }
                    Button("Continuar con la revisión…") {
                        let path = selectedPath, plugin = updating
                        Task { if await store.plugins.change(plugin == nil ? "install" : "update", plugin: plugin, path: path, package: package) { preview = nil } }
                    }.disabled(store.plugins.isChanging)
                }
                if let error = store.plugins.errorMessage { Text(error).foregroundStyle(.red) }
            }.padding(24).frame(width: 660)
        }
    }
    private func choosePackage(_ plugin: PluginDescriptor?) {
        let panel = NSOpenPanel()
        panel.directoryURL = store.access.workspacePath.map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.temporaryDirectory
        panel.title = "Elegir paquete de plugin de Jarvis"
        panel.message = "Elige un archivo ZIP o tar. No se importará ni ejecutará código del paquete."
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false; panel.resolvesAliases = false
        guard panel.runModal() == .OK, let path = panel.url?.path else { return }
        Task {
            if let package = await store.plugins.inspect(path: path) {
                selectedPath = path; updating = plugin; preview = package
            }
        }
    }
}

private struct PluginDetailsView: View {
    let plugin: PluginDescriptor
    @Bindable var store: PluginStore
    let canManage: Bool
    let scope: SessionScope?
    let selectedAgent: String?
    let onUpdate: () -> Void
    let onDraft: (String) -> Void
    @State private var resourcePath = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(plugin.name).font(.headline)
            Text(plugin.description)
            Text("\(plugin.version) · revisión \(plugin.revision) · \(plugin.enabled ? "habilitado" : "deshabilitado")").font(.caption)
            Text("\(plugin.skills) skills · \(plugin.resources) archivos · \(plugin.hooks) hooks · \(plugin.mcp) descriptores MCP").font(.caption)
            DisclosureGroup("Procedencia sin verificación") {
                Text(plugin.source).textSelection(.enabled)
                Text("Publicador declarado: \(plugin.publisher)")
                Text("SHA-256: \(plugin.hash)").font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
            }
            HStack {
                Button(plugin.enabled ? "Deshabilitar…" : "Habilitar…") { Task { await store.change(plugin.enabled ? "disable" : "enable", plugin: plugin) } }
                Button("Actualizar paquete…", action: onUpdate)
                Button("Quitar…", role: .destructive) { Task { await store.change("remove", plugin: plugin) } }
            }.disabled(!canManage || store.isChanging)
            Button("Consultar versiones") { Task { await store.loadVersions(plugin) } }.disabled(store.isChanging)
            ForEach(store.versions[plugin.id] ?? []) { version in
                HStack {
                    Text(version.version + (version.current ? " · actual" : "")).font(.caption)
                    if !version.current {
                        Button("Volver a esta versión…") { Task { await store.change("rollback", plugin: plugin, target: version.version) } }
                        Button("Retirar esta copia…") { Task { await store.change("retire_history", plugin: plugin, target: version.version) } }
                    }
                }.disabled(!canManage || store.isChanging)
            }
            DisclosureGroup("Leer un recurso del paquete") {
                Button("Consultar archivos y contribuciones") { Task { await store.loadCatalog(plugin) } }.disabled(store.isChanging)
                if let catalog = store.catalogs[plugin.id] {
                    ForEach((catalog["files"].array ?? []).compactMap { try? ExtensionFileDescriptor($0) }) { file in
                        Button("Leer \(file.path) · \(file.bytes) bytes") { Task { await store.readResource(plugin, path: file.path) } }
                            .disabled(store.isChanging)
                    }
                    ForEach((catalog["mcp"].array ?? []).compactMap { $0["server_id"].string }, id: \.self) { serverID in
                        Button("Revisar configuración MCP «\(serverID)»…") { Task { await store.configureMCP(plugin, serverID: serverID) } }
                            .disabled(!canManage || store.isChanging)
                    }
                    DisclosureGroup("Skills, prompts y hooks declarados") {
                        Text(ManagementValidation.json(.object(["skills": catalog["skills"], "prompts": catalog["prompts"], "hooks": catalog["hooks"]])))
                            .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                    }
                    ForEach((catalog["hooks"].array ?? []).compactMap { try? PluginHookDescriptor($0) }) { hook in
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Hook: \(hook.event)").font(.caption.weight(.semibold))
                            if let server = hook.serverID, let tool = hook.tool {
                                Text("\(server) → \(tool) · \(ManagementValidation.json(hook.arguments))").font(.caption).textSelection(.enabled)
                                Button("Solicitar este hook MCP…") {
                                    if let scope { Task { await store.runHook(plugin, hook: hook, scope: scope) } }
                                }.disabled(!canManage || !plugin.enabled || scope == nil || selectedAgent != plugin.agentID || store.isChanging)
                            } else if let prompt = hook.promptID,
                                      let path = (catalog["prompts"].array ?? []).first(where: { $0["prompt_id"].string == prompt })?["path"].string {
                                Button("Preparar contexto del hook de prompt") { Task { if let text = await store.promptContext(plugin, path: path) { onDraft(text) } } }
                                    .disabled(!canManage || store.isChanging)
                            }
                        }
                    }
                }
                TextField("Ruta relativa declarada", text: $resourcePath)
                Button("Leer este recurso") { Task { await store.readResource(plugin, path: resourcePath) } }
                    .disabled(store.isChanging || !ExtensionFileDescriptor.validPath(resourcePath))
                Text("Sólo archivos conservados dentro de esta versión; una ruta de procedencia no permite acceder a otros archivos.").font(.caption)
            }
        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
    }
}
