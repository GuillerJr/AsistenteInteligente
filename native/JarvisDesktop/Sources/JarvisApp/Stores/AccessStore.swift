import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class AccessStore {
    var tools: [ToolDescriptor] = []
    var approvals: [ApprovalOperation] = []
    var mediaApprovalDetails: [String: String] = [:]
    var workspacePath: String?
    var workspacePrivateDefault = false
    var workspaceAvailable = false
    var workspaceError: String?
    var documents: [MemoryDocument] = []
    var matches: [MemoryMatch] = []
    var memoryQuery = "" {
        didSet {
            if memoryQuery != oldValue {
                matches = []; verifiedCitations = []; memorySearchHasMore = false
                memorySearchHasResponse = false; errorMessage = nil
            }
        }
    }
    var memoryMode = ""
    var memoryQueryMode = MemoryRecallMode.lexical {
        didSet {
            if memoryQueryMode != oldValue {
                memoryGeneration += 1; matches = []; verifiedCitations = []
                memorySearchHasMore = false; isLoadingMemory = false; errorMessage = nil
                memorySearchHasResponse = false
            }
        }
    }
    var memorySearchHasMore = false
    var memorySearchHasResponse = false
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
            approvals = []; mediaApprovalDetails = [:]; documents = []; matches = []; verifiedCitations = []
            documentOffset = 0; memoryGeneration += 1
            isLoadingMemory = false
            memoryQuery = ""; memoryQueryMode = .lexical; memorySearchHasMore = false; memorySearchHasResponse = false; errorMessage = nil
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
            memoryQueryMode = .lexical; memorySearchHasMore = false; memorySearchHasResponse = false
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
        var details: [String: String] = [:]
        for operation in operations where ["media_inspect", "telegram_send_attachment"].contains(operation.toolName) {
            if let detail = try? await attachmentApprovalDetail(operation) { details[operation.id] = detail }
        }
        if context == scope { approvals = operations; mediaApprovalDetails = details }
    }

    func mayApprove(_ operation: ApprovalOperation) -> Bool {
        !["media_inspect", "telegram_send_attachment"].contains(operation.toolName) || mediaApprovalDetails[operation.id] != nil
    }

    private func attachmentApprovalDetail(_ operation: ApprovalOperation) async throws -> String {
        let args = try JSONDecoder().decode(RPCValue.self, from: Data(operation.argumentsJSON.utf8))
        guard let id = args["attachment_id"].string, let hash = args["sha256"].string else { throw RPCFailure.invalidResponse }
        let response = try await rpc("media.list", operation.scope.params)
        guard let rows = response["attachments"].array else { throw RPCFailure.invalidResponse }
        let attachments = try rows.map(MediaAttachment.init)
        guard let file = attachments.first(where: { $0.id == id && $0.sha256 == hash && $0.scope == operation.scope && !$0.revoked }) else { throw RPCFailure.invalidResponse }
        var detail = "Archivo: \(file.name) · \(file.bytes) bytes\nSHA-256: \(file.sha256)"
        if operation.toolName == "telegram_send_attachment" {
            let target = try await rpc("telegram.destination", ["session_id": .string(operation.scope.sessionID), "run_id": .string(operation.runID)])
            guard target["session_id"] == .string(operation.scope.sessionID), target["run_id"] == .string(operation.runID),
                  let chat = target["chat_id"].integer, chat != 0,
                  let kind = target["chat_type"].string, ["private", "group", "supergroup"].contains(kind),
                  let message = target["message_id"].integer, message > 0,
                  target["maximum_attachment_bytes"].integer == 5 * 1024 * 1024,
                  file.bytes <= 5 * 1024 * 1024 else { throw RPCFailure.invalidResponse }
            let thread = target["message_thread_id"].integer.map(String.init) ?? "sin tema"
            detail += "\nDestino fijado: Telegram \(kind), chat \(chat), tema \(thread), respuesta al mensaje \(message).\nSe enviarán los bytes de este archivo; una respuesta de Telegram no acredita su lectura por el destinatario."
        } else {
            detail += "\nAnálisis local; el texto extraído y la observación visual podrán enviarse a Codex. No ejecuta macros o enlaces y no verifica la veracidad del contenido."
        }
        return detail
    }

    func decide(_ operation: ApprovalOperation, allow: Bool, remember: String = "operation") async {
        guard context == operation.scope, !decidingIDs.contains(operation.id) else { return }
        if allow && ["media_inspect", "telegram_send_attachment"].contains(operation.toolName) {
            guard let detail = try? await attachmentApprovalDetail(operation),
                  context == operation.scope, approvals.contains(operation) else {
                errorMessage = "No se pudo confirmar el archivo o destino actual. No se autorizó la operación."; return
            }
            mediaApprovalDetails[operation.id] = detail
        }
        if operation.toolName == "telegram_send_attachment" && remember != "operation" { return }
        guard approvals.contains(operation) else {
            errorMessage = "La solicitud cambió o ya no está pendiente. No se envió la decisión."
            return
        }
        if allow && operation.isExpired(at: Date()) {
            errorMessage = "La solicitud ha caducado. Espera una solicitud nueva."
            return
        }
        guard ["operation", "session", "always"].contains(remember),
              allow || remember == "operation", !(operation.scope.temporary && remember == "always") else { return }
        if remember != "operation" {
            let native = ["desktop_action", "browser_action"].contains(operation.toolName)
            guard OwnerManagementReview.confirm(title: "Recordar permiso de herramienta",
                action: remember == "always" ? "Permitir y guardar este permiso" : "Permitir durante este chat",
                detail: "Herramienta: \(operation.toolName)\n\n\(operation.argumentsJSON)\n\n" +
                    (native ? "Este permiso admite nuevos textos de hasta 4096 bytes y referencias vigentes dentro del mismo destino y tipo de acción. No permite otra app, ventana, pestaña u origen."
                            : "Se recordarán únicamente estos argumentos exactos; su contenido no se duplicará en el registro del permiso.") +
                    (remember == "always" ? "\nSe conservará para este cliente, agente y carpeta hasta que lo retires en Mac y navegador. No se incluye en respaldos."
                                          : "\nSólo permanece en memoria durante esta conversación."),
                isCurrent: { context == operation.scope && approvals.contains(operation) && !operation.isExpired(at: Date()) }) else { return }
        }
        decidingIDs.insert(operation.id)
        defer { decidingIDs.remove(operation.id) }
        do {
            let result = try await rpc("approvals.decide", operation.decision(allow: allow, remember: remember))
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
        guard client?.isActive == true, !isChangingWorkspace else { return }
        isChangingWorkspace = true
        defer { isChangingWorkspace = false }
        let panel = NSOpenPanel()
        panel.title = "Elegir carpeta de trabajo de Jarvis"
        panel.message = "Jarvis podrá proponer herramientas sobre esta carpeta. Las escrituras requerirán tu aprobación."
        panel.prompt = "Usar esta carpeta"
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.allowsMultipleSelection = false; panel.resolvesAliases = false
        guard panel.runModal() == .OK, client?.isActive == true, let url = panel.url else { return }
        do {
            // The chooser is the owner's explicit selection. Send its physical
            // directory so macOS aliases such as /tmp do not become a second root.
            let chosenPath = url.resolvingSymlinksInPath().path
            let result = try await rpc("workspace.set", ["path": .string(chosenPath)])
            guard let path = result["workspace"]["path"].string, path.hasPrefix("/") else { throw RPCFailure.invalidResponse }
            workspacePath = path; documents = []; matches = []; verifiedCitations = []
            memoryQueryMode = .lexical; memorySearchHasMore = false; memorySearchHasResponse = false
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
        guard !query.isEmpty else { matches = []; memorySearchHasMore = false; memorySearchHasResponse = false; return }
        guard query.utf8.count <= 512 else { errorMessage = MemoryManualSearchPresentation.queryTooLarge; return }
        let requestedMode = memoryQueryMode
        let generation = memoryGeneration
        let workspace = workspacePath
        isLoadingMemory = true
        memorySearchHasMore = false; memorySearchHasResponse = false; errorMessage = nil
        defer { if generation == memoryGeneration { isLoadingMemory = false } }
        do {
            let request = try MemoryManualSearchRequest(scope: scope, query: query, mode: requestedMode)
            let result = try await MemoryManualSearchResponse(rpc("memory.search", request.params), requested: requestedMode)
            guard context == scope, generation == memoryGeneration, workspacePath == workspace,
                  memoryQueryMode == requestedMode, memoryQuery.trimmingCharacters(in: .whitespacesAndNewlines) == query else { return }
            matches = result.matches; memoryMode = result.mode; verifiedCitations = []
            memorySearchHasMore = result.hasMore; memorySearchHasResponse = true
        } catch {
            if context == scope, generation == memoryGeneration, workspacePath == workspace,
               memoryQueryMode == requestedMode, memoryQuery.trimmingCharacters(in: .whitespacesAndNewlines) == query {
                errorMessage = MemoryManualSearchPresentation.error(error)
            }
        }
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
