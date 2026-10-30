import Foundation
import Observation

@MainActor @Observable public final class ProductProjectsStore {
    public private(set) var scope: SessionScope?
    public private(set) var runID: String?
    public private(set) var rows: [ProductProjectDescriptor] = []
    public private(set) var selectedID: String?
    public private(set) var inspect: ProductProjectInspection?
    public private(set) var logText: String?
    public private(set) var worktreeText: String?
    public private(set) var github: ProductGitHubConnection?
    public private(set) var githubReview: ProductGitHubReview?
    public private(set) var isRequesting = false
    public private(set) var isChanging = false
    public private(set) var responseUnconfirmed = false
    public private(set) var errorMessage: String?
    public private(set) var notice: String?
    public private(set) var pendingRunID: String?
    public private(set) var pendingRequestID: String?
    public private(set) var lastSnapshotID: String?
    public private(set) var resultSummary: String?
    @ObservationIgnored private var request: MaintenanceClient.Request?
    @ObservationIgnored private var version = 0
    @ObservationIgnored private var selectionVersion = 0
    @ObservationIgnored private var mutationVersion = 0
    @ObservationIgnored private var pending: [SessionScope: Pending] = [:]
    @ObservationIgnored private var uncertain: Set<SessionScope> = []
    private struct Pending { let id: String; let tool: String }
    public init() {}
    public var selected: ProductProjectDescriptor? { rows.first { $0.id == selectedID } }
    public var blocksInteraction: Bool { isChanging || responseUnconfirmed || scope == nil }
    public var viewIdentity: String { "\(version):\(scope?.sessionID ?? "none"):\(runID ?? "host")" }

    public func configure(_ transport: LocalRPCClient) {
        configure(request: { try await transport.call($0, params: $1) })
    }
    public func configure(request: @escaping MaintenanceClient.Request) {
        version += 1; self.request = request; scope = nil; runID = nil
        pending = [:]; uncertain = []; clearScope()
    }
    public func setScope(_ scope: SessionScope?, runID: String? = nil) {
        guard self.scope != scope || self.runID != runID else { return }
        version += 1; self.scope = scope; self.runID = runID; clearScope()
        pendingRequestID = scope.flatMap { pending[$0]?.id }
        responseUnconfirmed = scope.map { uncertain.contains($0) } ?? false
    }
    private func clearScope() {
        rows = []; selectedID = nil; inspect = nil; logText = nil; worktreeText = nil
        github = nil; githubReview = nil; isRequesting = false; isChanging = false
        responseUnconfirmed = false; errorMessage = nil; notice = nil
        pendingRunID = nil; pendingRequestID = nil; lastSnapshotID = nil; resultSummary = nil
        selectionVersion += 1; mutationVersion += 1
    }
    public func close() { version += 1; request = nil; scope = nil; runID = nil; clearScope() }
    public func select(_ id: String?) {
        guard selectedID != id else { return }
        selectedID = rows.contains(where: { $0.id == id }) ? id : nil
        selectionVersion += 1; inspect = nil; logText = nil; worktreeText = nil
        errorMessage = nil
    }
    private func parameters(_ scope: SessionScope, kind: String,
                            arguments: [String: RPCValue]) -> [String: RPCValue] {
        var params = scope.params
        if let runID { params["run_id"] = .string(runID) }
        params["kind"] = .string(kind); params["arguments"] = .object(arguments)
        return params
    }
    public func refresh() async {
        guard let request, let scope, !isChanging, !isRequesting else { return }
        let current = version, mutation = mutationVersion
        isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            if let operation = pending[scope] {
                do {
                    var params = scope.params; params["run_id"] = .string(operation.id)
                    let result = try await request("product.tool.result", params)
                    guard let settled = result["settled"].bool,
                          result["provenance"].string == "external_unverified" else { throw RPCFailure.invalidResponse }
                    guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
                    pendingRunID = operation.id
                    if settled {
                        if result["payload"] != .null { try consume(result["payload"], tool: operation.tool) }
                        if let error = result["error"].string {
                            notice = error == "approval_denied" ? "La operación no fue aprobada."
                                : "La ejecución terminó. Revisa su recibo antes de repetir una acción."
                            if error != "approval_denied" { responseUnconfirmed = true; uncertain.insert(scope) }
                        } else if result["payload"] == .null {
                            notice = "La ejecución terminó sin un resultado disponible. Consulta su recibo."
                        }
                        pending.removeValue(forKey: scope); pendingRequestID = nil
                    }
                } catch {
                    guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
                    notice = "El resultado sigue sin estar disponible. Sólo se consultará su identidad; no se repetirá."
                }
            }
            let response = try await request("product.projects", parameters(scope, kind: "list", arguments: [:]))
            let projects = try ManagementValidation.rows(response["result"]["projects"], limit: 128,
                                                         parse: ProductProjectDescriptor.init)
            guard Set(projects.map(\.id)).count == projects.count else { throw RPCFailure.invalidResponse }
            let status = try await request("product.projects", parameters(scope, kind: "github.status", arguments: [:]))
            let connection = try ProductGitHubConnection(status["result"])
            guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
            rows = projects; github = connection
            if let selectedID, !rows.contains(where: { $0.id == selectedID }) { select(nil) }
            if selectedID == nil { select(rows.first?.id) }
            if !responseUnconfirmed { errorMessage = nil }
        } catch {
            guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
            errorMessage = "No se pudo consultar la lista de proyectos o el estado de GitHub. Actualiza para volver a consultar."
        }
    }
    public func pollWhileVisible() async {
        let current = version
        while current == version, !Task.isCancelled {
            await refresh()
            do { try await Task.sleep(for: .seconds(2)) } catch { return }
        }
    }
    public func inspectSelected() async { await inspectPage(offset: 0, previous: nil) }
    public func loadMoreChanges() async {
        guard let inspect, let offset = inspect.nextOffset else { return }
        await inspectPage(offset: offset, previous: inspect)
    }
    private func inspectPage(offset: Int, previous: ProductProjectInspection?) async {
        guard let request, let scope, let selectedID, !isRequesting, !isChanging else { return }
        let current = version, selection = selectionVersion, mutation = mutationVersion
        isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            let response = try await request("product.projects", parameters(scope, kind: "inspect",
                arguments: ["project_id": .string(selectedID), "offset": .number(Double(offset)), "limit": .number(50)]))
            let page = try ProductProjectInspection(response["result"])
            guard page.project.id == selectedID, page.offset == offset else { throw RPCFailure.invalidResponse }
            guard current == version, selection == selectionVersion, mutation == mutationVersion, !Task.isCancelled else { return }
            if let previous, previous.stateDigest != page.stateDigest || previous.head != page.head {
                inspect = nil; isRequesting = false; await inspectSelected()
                if current == version { notice = "El proyecto cambió. Se reinició la lectura para no mezclar versiones del diff." }
                return
            }
            inspect = page; errorMessage = nil
        } catch {
            guard current == version, selection == selectionVersion, !Task.isCancelled else { return }
            inspect = nil; errorMessage = "No se pudo inspeccionar esta versión del proyecto. Consulta de nuevo su estado."
        }
    }
    public func loadLog() async { await loadText(kind: "log", key: "log") }
    public func loadWorktrees() async { await loadText(kind: "worktrees", key: "worktrees") }
    private func loadText(kind: String, key: String) async {
        guard let request, let scope, let selectedID, !isRequesting, !isChanging else { return }
        let current = version, selection = selectionVersion
        isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            var args: [String: RPCValue] = ["project_id": .string(selectedID)]
            if kind == "log" { args["limit"] = .number(20) }
            let response = try await request("product.projects", parameters(scope, kind: kind, arguments: args))
            let value = response["result"]
            guard value["content_trust"].string == "repository_data" else { throw RPCFailure.invalidResponse }
            let text = try ProductProjectValidation.text(value[key], limit: 262144, empty: true)
            guard current == version, selection == selectionVersion, !Task.isCancelled else { return }
            if kind == "log" { logText = text.replacingOccurrences(of: "\0", with: " · ") }
            else { worktreeText = text.replacingOccurrences(of: "\0", with: "\n") }
            errorMessage = nil
        } catch {
            if current == version, selection == selectionVersion { errorMessage = "No se pudo consultar el historial o las copias de trabajo." }
        }
    }
    /// Credentials are transient arguments, never observable properties, logs,
    /// model tools, errors, user defaults or a resumable operation payload.
    public func connectGithub(token: String, owner: String, repository: String) async -> Bool {
        guard let request, let scope, !blocksInteraction, !isRequesting,
              !token.isEmpty, token.utf8.count <= 4096,
              ProductProjectValidation.repository(owner: owner, name: repository) else { return false }
        let current = version
        mutationVersion += 1; isChanging = true; errorMessage = nil; githubReview = nil
        defer { if current == version { isChanging = false } }
        do {
            let response = try await request("product.projects", parameters(scope, kind: "github.connect",
                arguments: ["token": .string(token), "owner": .string(owner), "repository": .string(repository)]))
            let connection = try ProductGitHubConnection(response["result"])
            guard connection.configured, connection.owner?.caseInsensitiveCompare(owner) == .orderedSame,
                  connection.repository?.caseInsensitiveCompare(repository) == .orderedSame else { throw RPCFailure.invalidResponse }
            guard current == version, !Task.isCancelled else { return false }
            github = connection; notice = "GitHub conectado en memoria para este destino explícito."
            return true
        } catch {
            guard current == version else { return false }
            github = nil
            errorMessage = "No se pudo confirmar la conexión con GitHub. Revisa el token y el destino; no se volverá a enviar automáticamente."
            return false
        }
    }
    public func disconnectGithub() async {
        guard let request, let scope, !isChanging, !isRequesting else { return }
        let current = version
        mutationVersion += 1; isChanging = true
        defer { if current == version { isChanging = false } }
        do {
            let response = try await request("product.projects", parameters(scope, kind: "github.disconnect", arguments: [:]))
            let result = try ProductGitHubConnection(response["result"])
            guard !result.configured else { throw RPCFailure.invalidResponse }
            guard current == version, !Task.isCancelled else { return }
            github = result; githubReview = nil; errorMessage = nil; notice = "Credencial de GitHub retirada."
        } catch {
            if current == version { errorMessage = "No se pudo confirmar la retirada de GitHub. Consulta su estado antes de continuar." }
        }
    }
    public func register(path: String, name: String) async -> String? {
        guard ManagementValidation.text(path, limit: 4096), path.hasPrefix("/"),
              ManagementValidation.text(name, limit: 256) else { return nil }
        return await start("project_register", arguments: ["path": .string(path), "name": .string(name)])
    }
    public func operate(kind: String, arguments: [String: RPCValue]) async -> String? {
        guard let selected, ["worktree.create", "worktree.archive", "worktree.restore", "file.restore"].contains(kind),
              kind != "worktree.archive" || (selected.owned && !selected.archived) else { return nil }
        return await start("project_operation", arguments: ["project_id": .string(selected.id),
            "kind": .string(kind), "arguments": .object(arguments)])
    }
    public func readPullRequest(number: Int) async -> String? {
        guard github?.configured == true, (1..<2_147_483_648).contains(number) else { return nil }
        return await start("github_pull_request", arguments: ["number": .number(Double(number))])
    }
    public func publish(title: String, body: String, head: String, base: String,
                        expectedHeadSHA: String) async -> String? {
        guard github?.configured == true, github?.destination != nil,
              ManagementValidation.text(title, limit: 256), ManagementValidation.text(body, limit: 16384, empty: true),
              ProductProjectValidation.branch(head), ProductProjectValidation.branch(base),
              expectedHeadSHA.count == 40, ProductProjectValidation.oid(expectedHeadSHA) else { return nil }
        return await start("github_publish", arguments: ["title": .string(title), "body": .string(body),
            "head": .string(head), "base": .string(base), "expected_head_sha": .string(expectedHeadSHA)])
    }
    private func start(_ tool: String, arguments: [String: RPCValue]) async -> String? {
        guard let request, let scope, !blocksInteraction, !isRequesting,
              pending[scope] == nil, !Task.isCancelled else { return nil }
        let current = version, id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        mutationVersion += 1; isChanging = true; errorMessage = nil; notice = nil
        pending[scope] = Pending(id: id, tool: tool); pendingRequestID = id
        var params = scope.params
        if let runID { params["run_id"] = .string(runID) }
        params["tool"] = .string(tool); params["arguments"] = .object(arguments); params["request_id"] = .string(id)
        defer { if current == version { isChanging = false } }
        do {
            let response = try await request("product.tool.start", params)
            guard response["run_id"].string == id, let status = response["status"].string,
                  ["queued", "running", "waiting_approval", "cancelling", "cleaning", "completed", "failed", "cancelled", "interrupted"].contains(status) else {
                throw RPCFailure.invalidResponse
            }
            guard current == version, !Task.isCancelled else { return nil }
            pendingRunID = id; notice = "Revisa la operación en la autorización de ejecución de Jarvis."
            return id
        } catch {
            let unknown = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
            if unknown { uncertain.insert(scope) } else { pending.removeValue(forKey: scope) }
            guard current == version else { return nil }
            responseUnconfirmed = unknown
            if !unknown { pendingRequestID = nil }
            errorMessage = unknown ? "El inicio quedó sin confirmar. Se conserva su identidad y no se volverá a iniciar."
                : "La operación fue rechazada. Revisa el destino y sus permisos."
            return nil
        }
    }
    private func consume(_ payload: RPCValue, tool: String) throws {
        guard payload != .null else { notice = "La ejecución terminó sin un resultado disponible. Consulta su recibo."; return }
        try ProductInteractionValidation.bounded(payload)
        if tool == "github_pull_request" {
            githubReview = try ProductGitHubReview(payload)
            resultSummary = "Pull request consultado para una revisión concreta de su head."
        } else if tool == "project_register" {
            let project = try ProductProjectDescriptor(payload)
            resultSummary = "Proyecto registrado: \(project.name)."
        } else {
            guard payload.object != nil else { throw RPCFailure.invalidResponse }
            if payload["snapshot_id"] != .null { lastSnapshotID = try ProductCoordinationValidation.id(payload["snapshot_id"]) }
            let known = payload["effect_status"].string == "known" && payload["succeeded"].bool == true
                && payload["verified"].bool == true
                && payload["operation_id"].string.map { ManagementValidation.identifier($0) } == true
            if known { resultSummary = "Operación asentada. Consulta los detalles en su recibo de ejecución." }
            else {
                resultSummary = "Efecto sin confirmar. Conserva el recibo y no repitas la operación."
                responseUnconfirmed = true
                if let scope { uncertain.insert(scope) }
            }
            if known && payload["original_preserved"].bool == true {
                resultSummary = "Copia guardada; la carpeta original se conserva porque tenía cambios."
            }
        }
    }
    public func acknowledgeUnconfirmed() {
        guard !isChanging, !rows.isEmpty else { return }
        responseUnconfirmed = false
        notice = "Se conserva la operación anterior sin confirmar. No se reenviará automáticamente."
    }
}
