import AppKit
import DesktopCore
import SwiftUI

struct MCPView: View {
    @Bindable var store: ChatStore
    @State private var showConfiguration = false
    @State private var selectedPrompt: MCPPendingPrompt?
    @State private var oauthServer: MCPServerSnapshot?
    @State private var selectedTemplate: MCPPendingTemplate?

    private var canManage: Bool { store.canChangeSession && !store.mcp.isChanging }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Text("Servidores MCP").font(.title2.weight(.semibold))
                    Spacer()
                    if store.mcp.isChanging || store.mcp.isLoading { ProgressView().controlSize(.small) }
                }
                Text("Conecta programas locales o servidores HTTP/SSE para añadir herramientas, recursos, prompts y Apps MCP.")
                    .foregroundStyle(.secondary)
                Text("Guardar una configuración no inicia el programa. Conectar requiere que revises el ejecutable, sus argumentos y la versión del protocolo.")
                    .font(.callout)
                HStack {
                    Button("Configurar servidor…") { showConfiguration = true }
                        .disabled(!canManage || store.mcp.supportedProtocols.isEmpty)
                    Button("Actualizar") { Task { await store.mcp.refreshFromUI() } }
                        .disabled(!store.ready || store.mcp.isChanging || store.mcp.isLoading)
                }
                if !store.canChangeSession && store.activeRunID != nil {
                    Text("Los servidores sólo pueden cambiar cuando hayan terminado las ejecuciones.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let error = store.mcp.errorMessage {
                    Text(error).foregroundStyle(.red).textSelection(.enabled)
                }
                if store.mcp.pendingContentRunID != nil {
                    Text("Lectura MCP pendiente de aprobación o resultado. La tarjeta de autorización aparece en la conversación.")
                    Button("Consultar la misma solicitud") { store.mcp.resumeContentRead() }
                }
                if let preview = store.mcp.contentPreview {
                    DisclosureGroup("Contenido externo sin verificación") {
                        Text(ManagementValidation.json(preview.payload)).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        Button("Añadir al borrador como datos externos") {
                            let addition = preview.quotedDraft
                            if store.draft.utf8.count + addition.utf8.count + 2 <= 32000 { store.draft += "\n\n" + addition }
                            else { store.mcp.errorMessage = "El contenido completo supera el límite del borrador. No se añadió ni truncó." }
                        }.disabled(!preview.settled || preview.errorCode != nil)
                    }
                }
                if let app = store.mcp.app {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("App MCP externa · \(app.serverID)").font(.headline)
                        Text("El contenido no está verificado. Las herramientas conservan su aprobación nativa.").font(.caption)
                        if let renderer = store.mcp.appRenderer { MCPAppSurface(renderer: renderer).frame(height: 480).id(app.id) }
                        else if !store.mcp.appCleanupUnconfirmed { ProgressView("Preparando contenido aislado") }
                        if let error = store.mcp.appError { Text(error).foregroundStyle(.red) }
                        Button("Cerrar App MCP") { Task { await store.mcp.closeApplication() } }
                    }
                }
                if store.mcp.servers.isEmpty {
                    Text(store.mcp.didLoad ? "No hay servidores configurados." : "El servicio aún no publicó su catálogo MCP.")
                        .foregroundStyle(.secondary)
                }
                ForEach(store.mcp.servers) { server in
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text(server.id).font(.headline)
                            Spacer()
                            Label(server.connected ? "Conectado" : "Desconectado",
                                  systemImage: server.connected ? "circle.fill" : "circle")
                                .font(.caption).foregroundStyle(server.connected ? .green : .secondary)
                        }
                        Text("\(server.configuration.transport.title) · protocolo \(server.configuration.protocolVersion.rawValue)").font(.caption)
                        DisclosureGroup(server.configuration.transport == .stdio ? "Programa y argumentos exactos" : "Destino y configuración pública") {
                            Text(server.configuration.transport == .stdio ? server.configuration.commandJSON : ManagementValidation.json(.object(server.configuration.params)))
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                        }
                        if server.configuration.transport != .stdio {
                            HStack {
                                Button("Autorizar OAuth…") { oauthServer = server }.disabled(!canManage)
                                if store.mcp.oauthStates[server.id]?["flow_id"].string != nil {
                                    Button("Comprobar acceso") { Task { await store.mcp.finishOAuth(server) } }
                                }
                                Button("Renovar autorización…") { Task { await store.mcp.refreshOAuth(server) } }.disabled(!canManage)
                                Button("Revocar OAuth…") { Task { await store.mcp.revokeOAuth(server) } }
                                    .disabled(!store.ready || store.mcp.isChanging)
                            }
                            if let state = store.mcp.oauthStates[server.id]?["status"].string {
                                Text(state == "connected" ? "Autorización conectada" : state == "failed" ? "El acceso no se completó" : "Esperando el acceso en el navegador")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        if server.connected {
                            Button("Consultar catálogo de recursos y prompts") { Task { await store.mcp.loadCatalog(server) } }
                            if let catalog = store.mcp.catalogs[server.id] {
                                MCPContentCatalogView(catalog: catalog, store: store.mcp, prompt: $selectedPrompt,
                                    template: $selectedTemplate,
                                    toolRunID: store.latestConversationRunID,
                                    receiptToolNames: Set(store.receipts.filter { $0.succeeded && !$0.unknown }.map(\.toolName)))
                            }
                        }
                        if server.connected {
                            Text("\(server.toolCount) herramientas publicadas · requieren aprobación por operación")
                                .font(.caption).foregroundStyle(.secondary)
                            if let hash = server.catalogHash {
                                Text("Catálogo SHA-256: \(hash)")
                                    .font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
                            }
                        }
                        if let error = server.errorCode {
                            Text(MCPPresentation.serverError(error)).font(.callout).foregroundStyle(.red)
                        }
                        HStack {
                            if server.connected {
                                Button("Desconectar") {
                                    Task { await store.mcp.disconnect(server); try? await store.access.refreshCatalog() }
                                }
                            } else {
                                Button("Conectar…") {
                                    Task { await store.mcp.connect(server); try? await store.access.refreshCatalog() }
                                }
                            }
                            Button("Quitar…", role: .destructive) {
                                Task { await store.mcp.remove(server); try? await store.access.refreshCatalog() }
                            }
                        }.disabled(!canManage)
                    }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
                }
                Divider()
                MCPRelayView(store: store)
                Divider()
                Text("Un servidor ejecuta un programa con tu usuario. Su HOME privado aísla configuración, pero no limita sus accesos como una sandbox del sistema. Las decisiones de sus herramientas se muestran en las mismas tarjetas de aprobación de la conversación.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(24)
        }
        .sheet(isPresented: $showConfiguration) {
            MCPConfigurationView(mcp: store.mcp, canManage: canManage)
        }
        .sheet(item: $selectedPrompt) { pending in MCPPromptArgumentsView(pending: pending, store: store.mcp) }
        .sheet(item: $oauthServer) { server in MCPOAuthView(server: server, store: store.mcp) }
        .sheet(item: $selectedTemplate) { pending in MCPTemplateView(pending: pending, store: store.mcp) }
        .task(id: store.mcpSelectionKey) {
            store.mcp.appSurfaceVisible = true
            await store.mcp.setContext(store.selected?.scope, bindingKey: store.mcpSelectionKey)
            await store.mcp.refreshFromUI()
        }
        .onDisappear {
            store.mcp.appSurfaceVisible = false
            Task { await store.mcp.closeApplication() }
        }
    }
}

private struct MCPPendingPrompt: Identifiable {
    let serverID: String
    let prompt: MCPPromptDescriptor
    var id: String { serverID + ":" + prompt.id }
}
private struct MCPContentCatalogView: View {
    let catalog: MCPContentCatalog
    @Bindable var store: MCPStore
    @Binding var prompt: MCPPendingPrompt?
    @Binding var template: MCPPendingTemplate?
    let toolRunID: String?
    let receiptToolNames: Set<String>
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Generación \(catalog.generation) · \(catalog.resources.count) recursos · \(catalog.prompts.count) prompts").font(.caption)
            ForEach(catalog.resources) { resource in
                HStack {
                    VStack(alignment: .leading) { Text(resource.name); Text(resource.uri).font(.caption).textSelection(.enabled) }
                    Spacer()
                    Button(resource.isApp ? "Abrir App…" : "Leer…") {
                        Task { await store.readContent(server: catalog.serverID, kind: "resource", name: resource.uri, app: resource.isApp) }
                    }.disabled(store.blocksContextChanges || store.pendingContentRunID != nil || store.context == nil)
                }
            }
            ForEach(catalog.resourceTemplates) { descriptor in
                HStack {
                    VStack(alignment: .leading) { Text(descriptor.name); Text(descriptor.uriTemplate).font(.caption).textSelection(.enabled) }
                    Spacer()
                    Button("Completar plantilla…") { template = MCPPendingTemplate(serverID: catalog.serverID, template: descriptor) }
                        .disabled(descriptor.isApp || store.blocksContextChanges || store.pendingContentRunID != nil || store.context == nil)
                }
                if descriptor.isApp { Text("La plantilla declara una App MCP y requiere una revisión nativa específica; no se leerá como texto para el modelo.").font(.caption).foregroundStyle(.secondary) }
            }
            ForEach(catalog.prompts) { descriptor in
                HStack {
                    VStack(alignment: .leading) { Text(descriptor.name); Text(descriptor.description).font(.caption) }
                    Spacer()
                    Button("Preparar prompt…") { prompt = MCPPendingPrompt(serverID: catalog.serverID, prompt: descriptor) }
                        .disabled(store.blocksContextChanges || store.pendingContentRunID != nil || store.context == nil)
                }
            }
            DisclosureGroup("Herramientas y visibilidad de Apps") {
                ForEach(catalog.tools) { tool in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(tool.name) · \(tool.appVisible ? "accesible desde Apps del mismo servidor" : "sólo modelo")").font(.caption)
                        if let uri = tool.resourceURI {
                            Button(receiptToolNames.contains(tool.publicName) ? "Abrir App con el resultado de este turno…" : "Abrir vista de la herramienta…") {
                                Task { await store.readContent(server: catalog.serverID, kind: "resource", name: uri, app: true,
                                    toolRunID: receiptToolNames.contains(tool.publicName) ? toolRunID : nil) }
                            }.disabled(store.blocksContextChanges || store.pendingContentRunID != nil || store.context == nil)
                        }
                    }
                }
            }
            Text("Una lectura también requiere autorización. El prompt es contenido externo y nunca sustituye instrucciones del sistema.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
private struct MCPPromptArgumentsView: View {
    let pending: MCPPendingPrompt
    @Bindable var store: MCPStore
    @Environment(\.dismiss) private var dismiss
    @State private var arguments: [String: String] = [:]
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Preparar prompt externo: \(pending.prompt.name)").font(.headline)
            ForEach(pending.prompt.arguments) { argument in
                TextField(argument.name + (argument.required ? " · obligatorio" : ""), text: Binding(
                    get: { arguments[argument.name] ?? "" }, set: { arguments[argument.name] = $0 }))
                Text(argument.description).font(.caption).foregroundStyle(.secondary)
            }
            Text("Se consultará al servidor con estos argumentos. El resultado se mostrará como datos externos; no se enviará automáticamente.").font(.caption)
            HStack {
                Button("Cancelar") { dismiss() }
                Button("Solicitar lectura…") {
                    let values = arguments.filter { !$0.value.isEmpty }.mapValues(RPCValue.string)
                    Task { await store.readContent(server: pending.serverID, kind: "prompt", name: pending.prompt.name, arguments: values) }
                    dismiss()
                }.disabled(store.blocksContextChanges || pending.prompt.arguments.contains { $0.required && (arguments[$0.name] ?? "").isEmpty }
                    || arguments.values.contains { $0.utf8.count > 8192 })
            }
        }.padding(24).frame(width: 580)
    }
}
private struct MCPOAuthView: View {
    let server: MCPServerSnapshot
    @Bindable var store: MCPStore
    @Environment(\.dismiss) private var dismiss
    @State private var clientID = ""
    @State private var scopes = ""
    @State private var discovery: MCPOAuthDiscovery?
    @State private var issuers: [String] = []
    @State private var selectedIssuer = ""
    @State private var discovering = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("OAuth público · \(server.id)").font(.headline)
            Text("Cliente público configurado: \(clientID)").font(.caption).textSelection(.enabled)
            Text("Scopes configurados: \(scopes.isEmpty ? "ninguno" : scopes)").font(.caption)
            if let discovery { Text("Recurso: \(discovery.resource)").font(.caption).textSelection(.enabled) }
            if issuers.count > 1 {
                Picker("Emisor de autorización", selection: $selectedIssuer) {
                    Text("Selecciona un emisor").tag("")
                    ForEach(issuers, id: \.self) { Text($0).tag($0) }
                }
                Text("El servidor publicó varios emisores. Jarvis no elige uno automáticamente.").font(.caption).foregroundStyle(.secondary)
                Button("Consultar el emisor elegido") { discover(issuer: selectedIssuer) }
                    .disabled(selectedIssuer.isEmpty || !issuers.contains(selectedIssuer) || discovering || store.isChanging)
            } else if let issuer = discovery?.issuer { Text("Emisor: \(issuer)").textSelection(.enabled) }
            if discovering { ProgressView("Consultando metadatos públicos") }
            Button("Volver a consultar emisores") { discover() }.disabled(discovering || store.isChanging)
            Text("No introduzcas un client secret ni tokens. Revisarás el emisor y completarás tú el acceso en el navegador.").font(.caption)
            if let error = store.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Button("Cancelar") { dismiss() }
                Button("Revisar inicio de sesión…") {
                    Task {
                        if await store.authorizeOAuth(server, clientID: clientID, scopes: scopes.split(whereSeparator: \.isWhitespace).map(String.init), issuer: selectedIssuer) { dismiss() }
                    }
                }.disabled(clientID.isEmpty || discovering || store.isChanging || discovery?.validatesSelection(selectedIssuer) != true)
            }
        }.padding(24).frame(width: 560)
        .onAppear { clientID = server.configuration.oauth?.clientID ?? ""; scopes = server.configuration.oauth?.scopes.joined(separator: " ") ?? ""; discover() }
    }
    private func discover(issuer: String? = nil) {
        discovering = true
        Task {
            let result = await store.discoverOAuth(server, issuer: issuer)
            if let result {
                if let issuer {
                    if selectedIssuer == issuer && result.validatesSelection(issuer) { discovery = result }
                } else {
                    discovery = result; issuers = result.issuers
                    selectedIssuer = result.requiresSelection ? "" : result.issuer ?? ""
                }
            }
            discovering = false
        }
    }
}

private struct MCPAppSurface: NSViewRepresentable {
    let renderer: MCPAppRenderer
    func makeNSView(context: Context) -> NSView {
        let container = NSView()
        if let view = renderer.webView {
            view.translatesAutoresizingMaskIntoConstraints = false; container.addSubview(view)
            NSLayoutConstraint.activate([view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                view.trailingAnchor.constraint(equalTo: container.trailingAnchor), view.topAnchor.constraint(equalTo: container.topAnchor),
                view.bottomAnchor.constraint(equalTo: container.bottomAnchor)])
        }
        return container
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
