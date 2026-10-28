import DesktopCore
import SwiftUI

struct MCPPendingTemplate: Identifiable {
    let serverID: String
    let template: MCPResourceTemplateDescriptor
    var id: String { serverID + ":" + template.id }
}
struct MCPTemplateView: View {
    let pending: MCPPendingTemplate
    @Bindable var store: MCPStore
    @Environment(\.dismiss) private var dismiss
    @State private var values: [String: String] = [:]
    @State private var expanded: String?
    @State private var previewing = false
    private var arguments: [String: RPCValue] { values.mapValues(RPCValue.string) }
    private var withinBudget: Bool {
        values.values.allSatisfy { $0.utf8.count <= 8192 && !$0.contains("\0") }
            && values.values.reduce(0, { $0 + $1.utf8.count }) <= 32768
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Completar recurso externo: \(pending.template.name)").font(.headline)
            Text(pending.template.uriTemplate).font(.caption).textSelection(.enabled)
            if pending.template.isApp { Text("Esta plantilla corresponde a una App MCP; requiere una revisión nativa específica y no admite lectura genérica.").foregroundStyle(.secondary) }
            ForEach(pending.template.variables, id: \.self) { variable in
                TextField(variable, text: Binding(get: { values[variable] ?? "" }, set: { values[variable] = $0; expanded = nil }))
            }
            Text("La previsualización sólo expande la plantilla localmente. Leer el recurso resuelto exige la aprobación habitual; no permite abrir rutas del Mac.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Previsualizar URI resuelta") {
                let captured = arguments
                previewing = true
                Task {
                    let result = await store.expandTemplate(server: pending.serverID, template: pending.template.uriTemplate, arguments: captured)
                    if arguments == captured { expanded = result }
                    previewing = false
                }
            }.disabled(previewing || !withinBudget || store.blocksContextChanges)
            if let expanded { Text(expanded).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
            if !withinBudget { Text("Los valores completos superan el límite de la plantilla; no se truncarán.").foregroundStyle(.red) }
            if let error = store.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Button("Cancelar") { dismiss() }
                Button("Solicitar lectura de esta plantilla…") {
                    let captured = arguments
                    Task { await store.readContent(server: pending.serverID, kind: "template", name: pending.template.uriTemplate, arguments: captured) }
                    dismiss()
                }.disabled(pending.template.isApp || expanded == nil || !withinBudget || store.blocksContextChanges || store.pendingContentRunID != nil)
            }
        }.padding(24).frame(width: 620)
    }
}
