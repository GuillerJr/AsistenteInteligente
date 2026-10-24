import DesktopCore
import SwiftUI

struct MCPView: View {
    @Bindable var store: ChatStore
    @State private var showConfiguration = false

    private var canManage: Bool { store.canChangeSession && !store.mcp.isChanging }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Text("Servidores MCP").font(.title2.weight(.semibold))
                    Spacer()
                    if store.mcp.isChanging || store.mcp.isLoading { ProgressView().controlSize(.small) }
                }
                Text("Conecta programas locales mediante stdio para añadir sus herramientas al catálogo de Jarvis.")
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
                        Text("Protocolo \(server.configuration.protocolVersion.rawValue)").font(.caption)
                        DisclosureGroup("Programa y argumentos exactos") {
                            Text(server.configuration.commandJSON)
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
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
                Text("Un servidor ejecuta un programa con tu usuario. Su HOME privado aísla configuración, pero no limita sus accesos como una sandbox del sistema. Las decisiones de sus herramientas se muestran en las mismas tarjetas de aprobación de la conversación.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(24)
        }
        .sheet(isPresented: $showConfiguration) {
            MCPConfigurationView(mcp: store.mcp, canManage: canManage)
        }
    }
}
