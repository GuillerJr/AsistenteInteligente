import AppKit
import DesktopCore
import SwiftUI

struct MCPRelayView: View {
    @Bindable var store: ChatStore
    @State private var exports: Set<String> = []
    @State private var token = ""
    var body: some View {
        DisclosureGroup("Jarvis como servidor MCP local") {
            VStack(alignment: .leading, spacing: 12) {
                Text("El relay escucha sólo en 127.0.0.1 y requiere un bearer. Sus llamadas conservan la sesión, los argumentos y las aprobaciones de Jarvis.")
                    .font(.caption).foregroundStyle(.secondary)
                if let relay = store.mcp.relay {
                    Text(relay.running ? "Servidor local activo" : "Servidor local detenido").font(.headline)
                    if relay.requiresReview {
                        Text("Revisa las exportaciones: el catálogo cambió. El acceso está revocado; detén el relay y revisa de nuevo las herramientas para activarlo.")
                            .font(.callout).foregroundStyle(.orange)
                    }
                    if let endpoint = relay.endpoint { Text(endpoint).textSelection(.enabled) }
                    Text("\(relay.exportCount) herramientas compartidas · \(relay.remoteClients) clientes locales").font(.caption)
                    if relay.running {
                        HStack {
                            Button("Detener…") { token = ""; Task { await store.mcp.changeRelay("stop") } }
                            Button("Revocar acceso…", role: .destructive) { token = ""; Task { await store.mcp.changeRelay("revoke") } }
                        }.disabled(store.mcp.isChanging)
                        if relay.tokenAvailable {
                            Button("Mostrar token protegido…") { Task { token = await store.mcp.ownerRelayToken() ?? "" } }
                            if !token.isEmpty {
                                SecureField("Token del relay local", text: $token).disabled(true)
                                Button("Copiar token para el cliente local") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(token, forType: .string) }
                                Button("Ocultar y retirar de este panel") { token = "" }
                            }
                        }
                    } else {
                        ForEach(store.access.tools) { tool in
                            Toggle(tool.name, isOn: Binding(get: { exports.contains(tool.name) }, set: { on in
                                if on { exports.insert(tool.name) } else { exports.remove(tool.name) }
                            }))
                        }
                        Button("Revisar herramientas y activar…") { Task { await store.mcp.changeRelay("start", exports: Array(exports)) } }
                            .disabled(!store.canChangeSession || exports.isEmpty || store.mcp.context == nil)
                    }
                }
                Button("Consultar estado del relay") { Task { await store.mcp.refreshRelay() } }.disabled(store.mcp.isChanging)
            }.padding(.top, 8)
        }
        .task {
            while !Task.isCancelled {
                await store.mcp.refreshRelay()
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
            }
        }
        .onChange(of: store.mcp.relay?.requiresReview) { _, required in if required == true { token = "" } }
        .onDisappear { token = "" }
    }
}
