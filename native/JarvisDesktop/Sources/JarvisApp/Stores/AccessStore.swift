import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class AccessStore {
    var tools: [ToolDescriptor] = []
    var approvals: [ApprovalOperation] = []
    var workspacePath: String?
    var workspacePrivateDefault = false
    var workspaceAvailable = false
    var workspaceError: String?
    var documents: [MemoryDocument] = []
    var matches: [MemoryMatch] = []
    var memoryQuery = ""
    var memoryMode = ""
    var verifiedCitations: Set<String> = []
    var errorMessage: String?
    var isLoadingMemory = false
    var isChangingWorkspace = false
    var decidingIDs: Set<String> = []
    var hasMoreDocuments = false
    var memoryEnabled = false
    var toolsEnabled = false

    @ObservationIgnored private var client: LocalRPCClient?
    @ObservationIgnored private var context: SessionScope?
    @ObservationIgnored private var documentOffset = 0
    @ObservationIgnored private var memoryGeneration = 0

    func configure(client: LocalRPCClient) { self.client = client }
    func close() async { await client?.close() }

    func setContext(_ scope: SessionScope?) {
        if scope != context {
            context = scope
            approvals = []; documents = []; matches = []; verifiedCitations = []
            documentOffset = 0; memoryGeneration += 1
            isLoadingMemory = false
            memoryQuery = ""; errorMessage = nil
        }
    }

    private func rpc(_ method: String, _ params: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard let client else { throw RPCFailure.unavailable }
        return try await client.call(method, params: params)
    }

    func refreshCapabilities(_ capabilities: RPCValue) {
        toolsEnabled = capabilities["tools"].bool == true
        memoryEnabled = capabilities["memory"].bool == true
    }

    func refreshCatalog() async throws {
        let result = try await rpc("tools.list")
        guard let values = result["tools"].array else { throw RPCFailure.invalidResponse }
        let descriptions = try values.map(ToolDescriptor.init)
        guard Set(descriptions.map(\.name)).count == descriptions.count else { throw RPCFailure.invalidResponse }
        tools = descriptions
        let workspace = try await rpc("workspace.get")
        guard workspace["workspace"].object != nil else { throw RPCFailure.invalidResponse }
        let path: String?
        if case .null = workspace["workspace"]["path"] { path = nil }
        else if let value = workspace["workspace"]["path"].string, value.hasPrefix("/") { path = value }
        else { throw RPCFailure.invalidResponse }
        guard let available = workspace["workspace"]["available"].bool,
              let privateDefault = workspace["workspace"]["private_default"].bool else { throw RPCFailure.invalidResponse }
        workspaceAvailable = available; workspacePrivateDefault = privateDefault
        workspaceError = workspace["workspace"]["error"].string
        if path != workspacePath {
            documents = []; matches = []; verifiedCitations = []; memoryQuery = ""
            documentOffset = 0; memoryGeneration += 1; isLoadingMemory = false
        }
        workspacePath = path
    }

    func refreshApprovals(runID: String?) async throws {
        guard let scope = context, toolsEnabled else { approvals = []; return }
        var parameters = scope.params
        if let runID { parameters["run_id"] = .string(runID) }
        let result = try await rpc("approvals.list", parameters)
        guard let values = result["approvals"].array else { throw RPCFailure.invalidResponse }
        let operations = try values.map { try ApprovalOperation($0, expectedScope: scope, expectedRunID: runID) }
        guard Set(operations.map(\.id)).count == operations.count else { throw RPCFailure.invalidResponse }
        if context == scope { approvals = operations }
    }

    func decide(_ operation: ApprovalOperation, allow: Bool) async {
        guard context == operation.scope, !decidingIDs.contains(operation.id) else { return }
        guard approvals.contains(operation) else {
            errorMessage = "La solicitud cambió o ya no está pendiente. No se envió la decisión."
            return
        }
        if allow && operation.isExpired(at: Date()) {
            errorMessage = "La solicitud ha caducado. Espera una solicitud nueva."
            return
        }
        decidingIDs.insert(operation.id)
        defer { decidingIDs.remove(operation.id) }
        do {
            let result = try await rpc("approvals.decide", operation.decision(allow: allow))
            guard let accepted = result["accepted"].bool else { throw RPCFailure.invalidResponse }
            if !accepted { errorMessage = "La solicitud ya no está vigente; no se autorizó ninguna acción nueva." }
            try await refreshApprovals(runID: operation.runID)
        } catch { report(error) }
    }

    func revokePending() async {
        guard let scope = context else { return }
        do {
            let result = try await rpc("approvals.revoke", scope.params)
            guard result["revoked"].bool == true else { throw RPCFailure.invalidResponse }
            approvals = []
        } catch { report(error) }
    }

    func chooseWorkspace() async {
        guard !isChangingWorkspace else { return }
        isChangingWorkspace = true
        defer { isChangingWorkspace = false }
        let panel = NSOpenPanel()
        panel.title = "Elegir carpeta de trabajo de Jarvis"
        panel.message = "Jarvis podrá proponer herramientas sobre esta carpeta. Las escrituras requerirán tu aprobación."
        panel.prompt = "Usar esta carpeta"
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.allowsMultipleSelection = false; panel.resolvesAliases = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            // The chooser is the owner's explicit selection. Send its physical
            // directory so macOS aliases such as /tmp do not become a second root.
            let chosenPath = url.resolvingSymlinksInPath().path
            let result = try await rpc("workspace.set", ["path": .string(chosenPath)])
            guard let path = result["workspace"]["path"].string, path.hasPrefix("/") else { throw RPCFailure.invalidResponse }
            workspacePath = path; documents = []; matches = []; verifiedCitations = []
            documentOffset = 0; memoryGeneration += 1
            isLoadingMemory = false
            try await refreshCatalog()
        } catch { report(error) }
    }

    func loadDocuments(reset: Bool = true) async {
        guard let scope = context, memoryEnabled, !isLoadingMemory else { return }
        let generation = memoryGeneration
        isLoadingMemory = true
        defer { if generation == memoryGeneration { isLoadingMemory = false } }
        do {
            var parameters = scope.params
            parameters["offset"] = .number(Double(reset ? 0 : documentOffset))
            let result = try await rpc("memory.list", parameters)
            guard let values = result["documents"].array,
                  let next = result["next_offset"].integer,
                  let more = result["has_more"].bool,
                  next >= (reset ? 0 : documentOffset),
                  !more || next > (reset ? 0 : documentOffset) else { throw RPCFailure.invalidResponse }
            let page = try values.map(MemoryDocument.init)
            guard context == scope, generation == memoryGeneration else { return }
            let combined = reset ? page : documents + page
            guard Set(combined.map(\.id)).count == combined.count else { throw RPCFailure.invalidResponse }
            documents = combined
            documentOffset = next; hasMoreDocuments = more
        } catch { if context == scope, generation == memoryGeneration { report(error) } }
    }

    func searchMemory() async {
        guard let scope = context, memoryEnabled, !isLoadingMemory else { return }
        let query = memoryQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { matches = []; return }
        let generation = memoryGeneration
        isLoadingMemory = true
        defer { if generation == memoryGeneration { isLoadingMemory = false } }
        do {
            var parameters = scope.params
            parameters["query"] = .string(query); parameters["offset"] = .number(0)
            let result = try await rpc("memory.search", parameters)
            guard let values = result["matches"].array,
                  let mode = result["mode"].string,
                  ["fts5_lexical", "lexical_scan"].contains(mode) else { throw RPCFailure.invalidResponse }
            let hits = try values.map(MemoryMatch.init)
            guard context == scope, generation == memoryGeneration else { return }
            matches = hits; memoryMode = mode; verifiedCitations = []
        } catch { if context == scope, generation == memoryGeneration { report(error) } }
    }

    func verifyCitation(_ match: MemoryMatch) async {
        guard let scope = context else { return }
        let generation = memoryGeneration
        do {
            var parameters = scope.params
            parameters["citation"] = match.citation
            let result = try await rpc("memory.resolve", parameters)
            guard result["verified"].bool == true, result["excerpt"].string == match.excerpt else {
                throw RPCFailure.invalidResponse
            }
            if context == scope, generation == memoryGeneration { verifiedCitations.insert(match.id) }
        } catch { if context == scope, generation == memoryGeneration { report(error) } }
    }

    private func report(_ error: Error) {
        errorMessage = CapabilityPresentation.errorMessage(error)
    }
}
