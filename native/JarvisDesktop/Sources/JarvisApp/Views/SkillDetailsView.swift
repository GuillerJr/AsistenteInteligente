import DesktopCore
import SwiftUI

struct SkillDetailsView: View {
    let skill: SkillDescriptor
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    @State private var metadata: SkillInvocationMetadata?
    @State private var versions: [ExtensionVersion] = []
    @State private var values: [String: String] = [:]
    @State private var invocation: SkillInvocation?
    @State private var resource: ExtensionResource?
    @State private var argumentError: String?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(skill.name).font(.title2.weight(.semibold))
                Text("Versión conservada · revisión \(skill.revision) · \(skill.hash)").font(.caption).textSelection(.enabled)
                if let metadata {
                    Text("Versión declarada: \(metadata.version)").font(.headline)
                    Text(ManagementValidation.json(metadata.metadata)).font(.caption).textSelection(.enabled)
                    if metadata.userInvocable {
                        Text("Preparar contexto con argumentos").font(.headline)
                        Text(metadata.hint).font(.caption)
                        ForEach(metadata.arguments) { argument in argumentInput(argument) }
                        Button("Preparar contexto para revisión") { prepare(metadata) }
                            .disabled(!store.canChangeSession || store.management.isChanging)
                        Text("Sólo prepara texto y argumentos como datos. No ejecuta scripts, no activa herramientas ni envía un turno.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else { Text("Esta skill no permite invocación manual.").font(.caption) }
                }
                if let error = argumentError ?? store.management.errorMessage { Text(error).foregroundStyle(.red) }
                if let invocation {
                    Text("Contexto externo para revisar").font(.headline)
                    Text(invocation.context).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                    Button("Añadir al borrador como contexto externo") {
                        let addition = invocation.quotedDraft
                        guard store.draft.utf8.count + addition.utf8.count + 2 <= 32000 else {
                            argumentError = "El contexto completo supera el límite del borrador; no se añadió ni truncó."; return
                        }
                        store.draft += "\n\n" + addition; dismiss()
                    }.disabled(!store.canChangeSession)
                }
                Divider()
                Text("Recursos conservados").font(.headline)
                ForEach(skill.resources, id: \.self) { path in
                    Button("Leer \(path)") { Task { resource = await store.management.resource(skill, path: path) } }
                        .disabled(store.management.isChanging)
                }
                if let resource {
                    Text("\(resource.path) · \(resource.data.count) bytes · \(resource.hash)").font(.caption).textSelection(.enabled)
                    if let text = resource.text { Text(text).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                    else { Text("Recurso binario verificado por hash. No se ejecutará ni abrirá.").font(.caption) }
                }
                Divider()
                Text("Versiones").font(.headline)
                ForEach(versions) { version in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(version.version) · revisión \(version.revision ?? 0)\(version.current ? " · actual" : "")").font(.caption)
                        if !version.current {
                            HStack {
                                Button("Restaurar deshabilitada…") { change(version, retire: false) }
                                Button("Retirar esta copia…", role: .destructive) { change(version, retire: true) }
                            }.disabled(!store.canChangeSession || store.management.isChanging)
                        }
                    }
                }
                Button("Cerrar") { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(24)
        }.frame(width: 680, height: 680)
        .task {
            metadata = await store.management.metadata(skill)
            versions = await store.management.versions(skill) ?? []
        }
    }
    @ViewBuilder private func argumentInput(_ argument: SkillArgumentDescriptor) -> some View {
        if argument.type == "boolean" {
            Picker(argument.name + (argument.required ? " · obligatorio" : ""), selection: Binding(
                get: { values[argument.name] ?? "" }, set: { values[argument.name] = $0 })) {
                Text("Sin valor").tag(""); Text("Sí").tag("true"); Text("No").tag("false")
            }
        } else {
            TextField(argument.name + (argument.required ? " · obligatorio" : ""), text: Binding(
                get: { values[argument.name] ?? "" }, set: { values[argument.name] = $0 }))
        }
        Text(argument.description).font(.caption).foregroundStyle(.secondary)
    }
    private func prepare(_ metadata: SkillInvocationMetadata) {
        do {
            var arguments: [String: RPCValue] = [:]
            for argument in metadata.arguments { if let value = try argument.value(values[argument.name] ?? "") { arguments[argument.name] = value } }
            argumentError = nil
            Task { invocation = await store.management.invoke(skill, arguments: arguments) }
        } catch { argumentError = "Revisa los argumentos obligatorios y sus tipos; el texto admite hasta 2048 bytes y el entero debe ser representable exactamente." }
    }
    private func change(_ version: ExtensionVersion, retire: Bool) {
        Task { if await store.management.changeVersion(skill, version: version, retire: retire) { dismiss() } }
    }
}
