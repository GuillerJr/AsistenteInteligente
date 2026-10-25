import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class MCPStore {
    var servers: [MCPServerSnapshot] = []
    var supportedProtocols: [MCPProtocolVersion] = []
    var errorMessage: String?
    var isChanging = false
    var isLoading = false
    var didLoad = false

    @ObservationIgnored private var runtimeIsActive: @Sendable () -> Bool = { false }
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var client: MCPManagementClient?

    func configure(client: LocalRPCClient) { self.client = MCPManagementClient(transport: client); runtimeIsActive = { client.isActive } }
    func close() async { await client?.close() }

    func refresh() async throws {
        guard runtimeIsActive(), !isChanging, !isLoading else { return }
        guard let client else { throw RPCFailure.unavailable }
        isLoading = true
        defer { isLoading = false }
        let version = generation
        let catalog = try await client.list()
        guard version == generation else { return }
        servers = catalog.servers
        supportedProtocols = catalog.supportedProtocols
        didLoad = true
    }

    func refreshFromUI() async {
        do { try await refresh(); errorMessage = nil }
        catch { report(error) }
    }

    func add(_ configuration: MCPConfiguration) async -> Bool {
        guard runtimeIsActive(), !isChanging, let client else { return false }
        guard supportedProtocols.contains(configuration.protocolVersion) else {
            errorMessage = MCPConfigurationError.protocolVersion.localizedDescription
            return false
        }
        generation += 1
        isChanging = true
        errorMessage = nil
        var succeeded = false
        do { try await client.add(configuration); succeeded = true }
        catch { report(error) }
        isChanging = false
        do { try await refresh() } catch { if errorMessage == nil { report(error) } }
        return succeeded
    }

    func connect(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, !server.connected, servers.contains(server), let client else { return }
        generation += 1
        isChanging = true
        defer { isChanging = false }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Conectar el servidor MCP «\(server.id)»"
        alert.informativeText = "Se ejecutará el programa de abajo con tu usuario. Un HOME privado aísla su configuración, pero no es una sandbox del sistema: el programa conserva los accesos de tu usuario.\n\nProtocolo: \(server.configuration.protocolVersion.rawValue). Las herramientas seguirán necesitando aprobación por operación. Revisa el array JSON completo; el primer elemento es el ejecutable y cada elemento posterior es un argumento exacto."
        alert.addButton(withTitle: "Conectar este programa")
        alert.addButton(withTitle: "Cancelar")
        alert.buttons.first?.keyEquivalent = ""
        alert.buttons.last?.keyEquivalent = "\r"
        alert.accessoryView = Self.commandReview(server.configuration.commandJSON)
        guard alert.runModal() == .alertFirstButtonReturn, runtimeIsActive() else { return }
        // This is only the owner's process-launch choice. Tool effects still
        // use the one run/session/digest authority in AccessStore and the host.
        errorMessage = nil
        do {
            let snapshot = try await client.connect(server.configuration)
            if let index = servers.firstIndex(where: { $0.id == server.id }) { servers[index] = snapshot }
            if !snapshot.connected { errorMessage = "El servidor no quedó conectado. Actualiza su estado antes de volver a intentarlo." }
        } catch { report(error) }
    }

    func disconnect(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, server.connected, servers.contains(server), let client else { return }
        generation += 1
        isChanging = true
        errorMessage = nil
        do { try await client.disconnect(server.id) }
        catch { report(error) }
        isChanging = false
        do { try await refresh() } catch { if errorMessage == nil { report(error) } }
    }

    func remove(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, servers.contains(server), let client else { return }
        generation += 1
        isChanging = true
        defer { isChanging = false }
        let alert = NSAlert()
        alert.messageText = "Quitar el servidor «\(server.id)»"
        alert.informativeText = "Se cerrará su conexión y se eliminará su configuración de Jarvis. No se borrará el programa."
        alert.addButton(withTitle: "Cancelar")
        alert.addButton(withTitle: "Quitar servidor")
        guard alert.runModal() == .alertSecondButtonReturn, runtimeIsActive() else { return }
        errorMessage = nil
        do {
            try await client.remove(server.id)
            servers.removeAll { $0.id == server.id }
        } catch { report(error) }
    }

    private static func commandReview(_ json: String) -> NSView {
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 560, height: 200))
        scroll.hasVerticalScroller = true; scroll.hasHorizontalScroller = true
        scroll.borderType = .bezelBorder
        let text = NSTextView(frame: scroll.bounds)
        text.isEditable = false; text.isSelectable = true
        text.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        text.isRichText = false; text.isHorizontallyResizable = true; text.isVerticallyResizable = true
        text.minSize = .zero
        text.maxSize = NSSize(width: 100_000, height: 100_000)
        text.textContainer?.widthTracksTextView = false
        text.textContainer?.containerSize = NSSize(width: 100_000, height: 100_000)
        text.string = json
        text.setAccessibilityLabel("Programa y argumentos exactos en JSON")
        scroll.documentView = text
        return scroll
    }

    private func report(_ error: Error) {
        errorMessage = MCPPresentation.errorMessage(error)
    }
}
