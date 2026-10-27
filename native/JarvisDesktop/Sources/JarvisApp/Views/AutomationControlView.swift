import AppKit
import ApplicationServices
import CoreGraphics
import DesktopCore
import SwiftUI

struct AutomationControlView: View {
    @Bindable var store: ChatStore
    @State private var document: AutomationTestDocument?

    var body: some View {
        @Bindable var host = store.automation
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Mac y navegador").font(.headline)
                Text(host.statusText).font(.callout)
                Text("Cada herramienta identifica su destino y exige una aprobación o un permiso guardado. Una acción sin resultado confirmado no se repite.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Actualizar estado y permisos") { Task { await host.refresh() } }
                    .disabled(host.lifecycleBusy)
                Button("Reconectar herramientas…") {
                    guard OwnerManagementReview.confirm(title: "Reconectar herramientas nativas",
                        action: "Cerrar y reconectar herramientas",
                        detail: "Cerrará las pestañas administradas y sus sesiones privadas; las pestañas adjuntas se desconectarán. Esperará la limpieza pendiente y abrirá un canal nuevo. No repetirá herramientas ni reabrirá sitios.",
                        isCurrent: { store.ready && !host.lifecycleBusy }) else { return }
                    Task { await host.reconnect() }
                }.disabled(!store.ready || host.lifecycleBusy)
                if let status = host.desktopStatus.object {
                    Text("Permisos del Mac").font(.subheadline.bold())
                    Text(Self.permissionText(status)).font(.caption).textSelection(.enabled)
                    Text("Accesibilidad permite observar y usar controles. Grabación de pantalla permite enviar un frame de la ventana elegida al proveedor seleccionado; Jarvis no crea un archivo de captura ni lo guarda en su historial SQL. El dueño concede estos permisos en Ajustes del Sistema.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Solicitar Accesibilidad") {
                            _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
                        }
                        Button("Solicitar observación de pantalla") { _ = CGRequestScreenCaptureAccess() }
                    }
                }
                Divider()
                Text("Buscador elegido").font(.subheadline.bold())
                Text("Elige explícitamente un servicio. Se enviará el texto de la consulta a esa URL sólo al autorizar browser_search.")
                    .font(.caption)
                TextField("URL con {query}", text: $host.searchTemplate)
                    .textFieldStyle(.roundedBorder)
                    .disabled(host.lifecycleBusy)
                HStack {
                    Menu("Elegir servicio…") {
                        Button("DuckDuckGo") { host.searchTemplate = "https://duckduckgo.com/?q={query}" }
                        Button("Bing") { host.searchTemplate = "https://www.bing.com/search?q={query}" }
                        Button("Google") { host.searchTemplate = "https://www.google.com/search?q={query}" }
                        Button("Sin buscador") { host.searchTemplate = "" }
                    }.disabled(host.lifecycleBusy)
                    Button("Revisar elección…") {
                        let current = host.searchTemplate
                        guard OwnerManagementReview.confirm(title: "Elegir buscador",
                            action: current.isEmpty ? "Retirar el buscador" : "Guardar este buscador",
                            detail: current.isEmpty ? "Buscar permanecerá deshabilitado hasta otra elección explícita."
                                : "Destino exacto:\n\n\(current)\n\nLas búsquedas autorizadas usarán únicamente este servicio. No se enviarán cookies del navegador personal.",
                            isCurrent: { host.connected && !host.lifecycleBusy && host.searchTemplate == current }) else { return }
                        Task { await host.saveSearch() }
                    }.disabled(!host.connected || host.lifecycleBusy || store.busy)
                }
                Divider()
                Text("Permisos guardados").font(.subheadline.bold())
                Text("Ligados a la clave del cliente, agente, carpeta y destino. Los respaldos no los trasladan a otros perfiles.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(permissionRows, id: \.id) { row in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(row.tool).font(.caption.bold())
                        Text(row.destination).font(.caption).textSelection(.enabled)
                        Text("Agente: \(row.agent) · \(row.id.prefix(12))").font(.caption2).foregroundStyle(.secondary)
                        Button("Retirar este permiso…") {
                            guard OwnerManagementReview.confirm(title: "Retirar permiso",
                                action: "Retirar este permiso", detail: "\(row.tool)\n\(row.destination)\n\nInvalidará los permisos emitidos y solicitará cancelar las ejecuciones de este cliente. No deshace los efectos anteriores.",
                                isCurrent: { host.connected && !host.lifecycleBusy && permissionRows.contains(where: { $0.id == row.id }) }) else { return }
                            Task { await host.revoke(row.id) }
                        }.disabled(!host.connected || host.lifecycleBusy)
                    }.padding(10).background(.quaternary, in: .rect(cornerRadius: 8))
                }
                if permissionRows.isEmpty { Text("No hay permisos persistentes.").font(.caption) }
                if host.testMode {
                    Divider()
                    Text("Perfil sintético de prueba").font(.subheadline.bold())
                    Button("Abrir documento sintético") {
                        if document == nil { document = AutomationTestDocument() }
                        document?.showWindow(nil); document?.window?.makeKeyAndOrderFront(nil)
                    }
                    Text("Contiene sólo texto artificial. Su ventana pertenece a este Jarvis de prueba y no abre documentos personales.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let error = host.errorMessage {
                    Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                }
            }.padding()
        }.task { await store.automation.refresh() }
    }

    private struct PermissionRow {
        let id: String; let tool: String; let destination: String; let agent: String
    }
    private var permissionRows: [PermissionRow] {
        ((store.automation.permissions["rules"].array ?? []) +
         (store.automation.permissions["volatile_rules"].array ?? [])).compactMap { row in
            guard let id = row["id"].string, ManagementValidation.identifier(id),
                  let tool = row["tool"].string, let destination = row["destination"].string,
                  let agent = row["agent_id"].string else { return nil }
            return PermissionRow(id: id, tool: tool, destination: destination, agent: agent)
        }
    }
    private static func permissionText(_ values: [String: RPCValue]) -> String {
        [("Accesibilidad", "accessibility"), ("Observación de pantalla", "screen_recording")].map { title, key in
            "\(title): \(values[key]?.bool == true ? "disponible" : "no concedido")"
        }.joined(separator: "\n")
    }
}

/// Tiny AppKit fixture: its controls exercise the actual native adapter without
/// opening TextEdit or reading documents from the owner's account.
@MainActor private final class AutomationTestDocument: NSWindowController {
    private let counter = NSTextField(labelWithString: "CONTADOR 0")
    private var count = 0
    init() {
        let window = NSWindow(contentRect: NSRect(x: 100, y: 160, width: 560, height: 380),
            styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Documento sintético Jarvis"
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.automation.fixture")
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 560, height: 380))
        let editor = NSTextView(frame: NSRect(x: 20, y: 105, width: 520, height: 250))
        editor.isRichText = false; editor.string = "JARVIS DOCUMENTO SINTETICO"
        editor.setAccessibilityLabel("Editor de prueba Jarvis")
        editor.setAccessibilityIdentifier("jarvis.fixture.editor")
        content.addSubview(editor)
        counter.frame = NSRect(x: 20, y: 60, width: 220, height: 22)
        counter.setAccessibilityIdentifier("jarvis.fixture.counter")
        content.addSubview(counter)
        let button = NSButton(title: "Sumar marcador", target: nil, action: nil)
        button.frame = NSRect(x: 270, y: 55, width: 200, height: 32)
        button.setAccessibilityIdentifier("jarvis.fixture.increment")
        content.addSubview(button)
        let secure = NSSecureTextField(frame: NSRect(x: 20, y: 18, width: 220, height: 26))
        secure.placeholderString = "Campo protegido sintético"
        secure.setAccessibilityIdentifier("jarvis.fixture.secure")
        content.addSubview(secure)
        window.contentView = content
        super.init(window: window)
        button.target = self; button.action = #selector(increment)
    }
    required init?(coder: NSCoder) { nil }
    @objc private func increment() { count += 1; counter.stringValue = "CONTADOR \(count)" }
}
