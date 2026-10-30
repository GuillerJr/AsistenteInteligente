import Foundation
import Observation

@MainActor @Observable public final class ProductCoordinationStore {
    public private(set) var scope: SessionScope?
    public private(set) var runID: String?
    public private(set) var snapshot: ProductCoordinationSnapshot?
    public private(set) var isRequesting = false
    public private(set) var isChanging = false
    public private(set) var responseUnconfirmed = false
    public private(set) var errorMessage: String?
    public private(set) var notice: String?
    public private(set) var pendingRunID: String?
    public private(set) var pendingRequestID: String?
    @ObservationIgnored private var request: MaintenanceClient.Request?
    @ObservationIgnored private var version = 0
    @ObservationIgnored private var mutationVersion = 0
    private var initialWorkspaces: [ProductWorkspaceDescriptor] = []
    @ObservationIgnored private var uncertain: Set<Submission> = []
    @ObservationIgnored private var pending: [SessionScope: String] = [:]
    @ObservationIgnored private var observedSettled: [SessionScope: String] = [:]
    private struct Submission: Hashable {
        let scope: SessionScope
        let kind: String
        let digest: String
    }
    public init() {}
    public var workspaces: [ProductWorkspaceDescriptor] { snapshot?.workspaces ?? initialWorkspaces }
    public var sessions: [ProductSessionBinding] { snapshot?.sessions ?? [] }
    public var blocksInteraction: Bool { isChanging || responseUnconfirmed || scope == nil }
    public var viewIdentity: String { "\(version):\(scope?.sessionID ?? "none"):\(runID ?? "host")" }
    public var isOwner: Bool { snapshot?.isOwner == true }
    public var mayWrite: Bool { snapshot?.mayWrite == true }
    public var unsettledRequests: [SessionScope: String] {
        pending.filter { observedSettled[$0.key] != $0.value }
    }
    public func observeRunReceipt(_ receipt: ConversationRun, scope: SessionScope) {
        if pending[scope] == receipt.id, !receipt.active { observedSettled[scope] = receipt.id }
    }

    public func configure(_ transport: LocalRPCClient) {
        configure(request: { try await transport.call($0, params: $1) })
    }
    public func configure(request: @escaping MaintenanceClient.Request) {
        version += 1; mutationVersion += 1; self.request = request
        scope = nil; runID = nil; snapshot = nil; initialWorkspaces = []
        pending = [:]; observedSettled = [:]; uncertain = []; pendingRunID = nil; pendingRequestID = nil
        isRequesting = false; isChanging = false; responseUnconfirmed = false
        errorMessage = nil; notice = nil
    }
    public func setScope(_ scope: SessionScope?, runID: String? = nil) {
        guard self.scope != scope || self.runID != runID else { return }
        version += 1; self.scope = scope; self.runID = runID; snapshot = nil
        initialWorkspaces = []; isRequesting = false; isChanging = false
        errorMessage = nil; notice = nil; pendingRunID = nil
        pendingRequestID = scope.flatMap { pending[$0] }
        responseUnconfirmed = scope.map { selected in uncertain.contains { $0.scope == selected } } ?? false
    }
    public func close() {
        version += 1; request = nil; scope = nil; runID = nil; snapshot = nil
        initialWorkspaces = []; isRequesting = false; isChanging = false
        pendingRunID = nil; pendingRequestID = nil; responseUnconfirmed = false
    }
    private func parameters(_ scope: SessionScope) -> [String: RPCValue] {
        var parameters = scope.params
        if let runID { parameters["run_id"] = .string(runID) }
        return parameters
    }
    public func refresh() async {
        guard let request, !isRequesting, !isChanging else { return }
        let current = version, mutation = mutationVersion, selected = scope
        isRequesting = true
        defer { if version == current { isRequesting = false } }
        do {
            guard let selected else {
                let response = try await request("product.initialize", [:])
                let rows = try ManagementValidation.rows(response["workspaces"], limit: 64,
                                                          parse: ProductWorkspaceDescriptor.init)
                guard Set(rows.map(\.id)).count == rows.count,
                      rows.allSatisfy({ !$0.temporary }) else { throw RPCFailure.invalidResponse }
                guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
                initialWorkspaces = rows; errorMessage = nil; return
            }
            if let id = pending[selected] {
                do {
                    var params = selected.params; params["run_id"] = .string(id)
                    let result = try await request("product.tool.result", params)
                    guard let settled = result["settled"].bool,
                          result["provenance"].string == "external_unverified" else {
                        throw RPCFailure.invalidResponse
                    }
                    guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
                    pendingRunID = id
                    if settled {
                        pending.removeValue(forKey: selected)
                        pendingRequestID = nil
                        if let error = result["error"].string {
                            notice = error == "approval_denied" ? "El envío no fue aprobado."
                                : "La ejecución terminó. Revisa su recibo antes de volver a enviar."
                        } else if result["payload"] != .null {
                            let message = try ProductAgentMessage(result["payload"])
                            guard message.sourceID == selected.sessionID,
                                  message.temporary == selected.temporary else { throw RPCFailure.invalidResponse }
                            notice = "Mensaje registrado en la bandeja local. Su recepción no acredita acciones."
                        }
                    }
                } catch {
                    guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
                    // Missing volatile result or a lost start response cannot
                    // prevent inspecting current metadata and cannot cause a
                    // second start. Keep the original request identity.
                    notice = "El recibo de envío sigue sin estar disponible. No se volverá a iniciar."
                }
            }
            let response = try await request("product.snapshot", parameters(selected))
            let observed = try ProductCoordinationSnapshot(response, expectedScope: selected,
                                                            expectedRunID: runID)
            guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
            snapshot = observed
            if !responseUnconfirmed { errorMessage = nil }
        } catch {
            guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
            snapshot = nil; errorMessage = Self.message(error)
        }
    }
    public func pollWhileVisible() async {
        let current = version
        while current == version, !Task.isCancelled {
            await refresh()
            do { try await Task.sleep(for: .seconds(2)) } catch { return }
        }
    }
    /// Retire a warning after inspecting state; the uncertain submission stays
    /// in the local ledger and its exact mutation is never sent again.
    public func acknowledgeUnconfirmed() {
        guard !isChanging, snapshot != nil else { return }
        responseUnconfirmed = false
        notice = "Se conserva el envío anterior sin confirmar. No se reenviará."
    }
    private func submission(_ kind: String, arguments: [String: RPCValue], scope: SessionScope) throws -> Submission {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let json = String(decoding: try encoder.encode(RPCValue.object(arguments)), as: UTF8.self)
        return Submission(scope: scope, kind: kind, digest: ManagementValidation.digest(json))
    }
    private func perform(_ kind: String, arguments: [String: RPCValue],
                         validate: (RPCValue) throws -> Void) async -> Bool {
        guard let request, let scope, !blocksInteraction, !isRequesting, !Task.isCancelled else { return false }
        let key: Submission
        do { key = try submission(kind, arguments: arguments, scope: scope) }
        catch { errorMessage = Self.message(error); return false }
        guard !uncertain.contains(key) else {
            responseUnconfirmed = true
            errorMessage = "Este cambio quedó sin confirmar y no se repetirá. Consulta el estado actual."
            return false
        }
        let current = version
        mutationVersion += 1; isChanging = true; errorMessage = nil; notice = nil
        var params = parameters(scope)
        params["kind"] = .string(kind); params["arguments"] = .object(arguments)
        defer { if current == version { isChanging = false } }
        do {
            let response = try await request("product.coordination", params)
            try validate(response["result"])
            guard current == version, !Task.isCancelled else { return false }
            isChanging = false; await refresh()
            return current == version && !Task.isCancelled
        } catch {
            if MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error) { uncertain.insert(key) }
            guard current == version else { return false }
            responseUnconfirmed = uncertain.contains(key); snapshot = nil
            errorMessage = responseUnconfirmed ? "La respuesta se interrumpió. Consulta el estado; el cambio no se repetirá."
                : Self.message(error)
            return false
        }
    }
    public func registerWorkspace(path: String, name: String) async -> Bool {
        guard isOwner, ManagementValidation.text(path, limit: 4096), path.hasPrefix("/"),
              ManagementValidation.text(name, limit: 128), let scope else { return false }
        return await perform("workspace.register", arguments: ["path": .string(path), "name": .string(name)]) { value in
            let row = try ProductWorkspaceDescriptor(value)
            guard row.path == path, row.name == name, row.temporary == scope.temporary else {
                throw RPCFailure.invalidResponse
            }
        }
    }
    public func bind(workspaceID: String, group: String?, pinned: Bool) async -> Bool {
        guard let snapshot, isOwner, workspaces.contains(where: { $0.id == workspaceID }),
              group == nil || ManagementValidation.text(group!, limit: 128) else { return false }
        let binding = snapshot.binding
        return await perform("session.bind", arguments: ["workspace_id": .string(workspaceID),
            "group": group.map(RPCValue.string) ?? .null, "pinned": .bool(pinned),
            "expected_revision": .number(Double(binding.revision))]) { value in
                let result = try ProductSessionBinding(value)
                guard result.scope == binding.scope, result.workspaceID == workspaceID,
                      result.group == group, result.pinned == pinned,
                      result.revision == binding.revision + 1 else { throw RPCFailure.invalidResponse }
            }
    }
    public func setMember(clientID: String, role: String) async -> Bool {
        guard let snapshot, isOwner, ProductCoordinationValidation.clientID(clientID),
              ["read", "collaborate", "remove"].contains(role) else { return false }
        let binding = snapshot.binding
        return await perform("session.member", arguments: ["member_id": .string(clientID),
            "role": .string(role), "expected_revision": .number(Double(binding.revision))]) { value in
                let result = try ProductSessionBinding(value)
                guard result.scope == binding.scope, result.ownerID == binding.ownerID,
                      result.revision == binding.revision + 1 else { throw RPCFailure.invalidResponse }
                let member = result.members.first { $0.id == clientID }
                guard role == "remove" ? member == nil : member?.role == role else { throw RPCFailure.invalidResponse }
            }
    }
    public func configureBudget(tokens: String, cost: String) async -> Bool {
        guard let snapshot, isOwner else { return false }
        let tokenText = tokens.trimmingCharacters(in: .whitespacesAndNewlines)
        let costText = cost.trimmingCharacters(in: .whitespacesAndNewlines)
        let tokenLimit = tokenText.isEmpty ? nil : Int(tokenText)
        guard tokenText.isEmpty || (tokenText.allSatisfy(\.isNumber) && tokenLimit != nil
            && tokenLimit! >= 0 && tokenLimit! <= 9_007_199_254_740_991),
              costText.isEmpty || ProductCoordinationValidation.cost(costText) else {
            errorMessage = "Escribe tokens enteros y un coste USD exacto con punto decimal, o deja el límite vacío."
            return false
        }
        let revision = snapshot.budget?.revision ?? 0
        return await perform("budget.configure", arguments: ["token_limit": tokenLimit.map { .number(Double($0)) } ?? .null,
            "cost_limit": costText.isEmpty ? .null : .string(costText), "currency": .string("USD"),
            "expected_revision": .number(Double(revision))]) { value in
                let result = try ProductBudgetSnapshot(value)
                guard result.scope == snapshot.binding.scope, result.tokenLimit == tokenLimit,
                      result.costLimit == (costText.isEmpty ? nil : costText),
                      result.revision == revision + 1 else { throw RPCFailure.invalidResponse }
            }
    }
    public func settle(message: ProductAgentMessage, accepted: Bool) async -> Bool {
        guard let snapshot, mayWrite, snapshot.inbox.contains(message), message.maySettle else { return false }
        let status = accepted ? "acknowledged" : "rejected"
        return await perform("message.settle", arguments: ["message_id": .string(message.id),
            "status": .string(status), "expected_revision": .number(Double(message.revision))]) { value in
                let result = try ProductAgentMessage(value)
                guard result.id == message.id, result.targetID == message.targetID,
                      result.status == status, result.revision == message.revision + 1 else { throw RPCFailure.invalidResponse }
            }
    }
    public func revoke(message: ProductAgentMessage) async -> Bool {
        guard let scope, let snapshot, mayWrite, message.sourceID == scope.sessionID,
              message.senderID == snapshot.clientID, message.maySettle else { return false }
        return await perform("message.revoke", arguments: ["message_id": .string(message.id),
            "expected_revision": .number(Double(message.revision))]) { value in
                let result = try ProductAgentMessage(value)
                guard result.id == message.id, result.status == "revoked",
                      result.revision == message.revision + 1 else { throw RPCFailure.invalidResponse }
            }
    }
    public func send(to target: SessionScope, clientID: String, body: String,
                     replyTo: ProductAgentMessage? = nil) async -> String? {
        guard let request, let scope, mayWrite, !blocksInteraction, !isRequesting,
              pending[scope] == nil,
              target.temporary == scope.temporary, ManagementValidation.identifier(target.sessionID),
              ProductCoordinationValidation.clientID(clientID),
              ManagementValidation.text(body, limit: 8000), !Task.isCancelled else { return nil }
        if let replyTo {
            guard snapshot?.inbox.contains(replyTo) == true, target.sessionID == replyTo.sourceID,
                  clientID == replyTo.senderID else { return nil }
        }
        let id = Self.newID(), messageID = Self.newID()
        var arguments: [String: RPCValue] = ["recipient_session_id": .string(target.sessionID),
            "recipient_temporary": .bool(target.temporary), "recipient_client_id": .string(clientID),
            "request_id": .string(messageID), "correlation_id": .string(replyTo?.correlationID ?? Self.newID()),
            "body": .string(body)]
        if let replyTo { arguments["parent_message_id"] = .string(replyTo.id) }
        let key: Submission
        do { key = try submission("message.send", arguments: arguments, scope: scope) }
        catch { errorMessage = Self.message(error); return nil }
        let current = version
        mutationVersion += 1; isChanging = true; errorMessage = nil; notice = nil
        pending[scope] = id; pendingRequestID = id
        var params = parameters(scope)
        params["tool"] = .string("agent_message_send"); params["arguments"] = .object(arguments)
        params["request_id"] = .string(id)
        defer { if current == version { isChanging = false } }
        do {
            let result = try await request("product.tool.start", params)
            guard result["run_id"].string == id, let status = result["status"].string,
                  ["queued", "running", "waiting_approval", "cancelling", "cleaning", "completed", "failed", "cancelled", "interrupted"].contains(status) else {
                throw RPCFailure.invalidResponse
            }
            guard current == version, !Task.isCancelled else { return nil }
            pendingRunID = id; notice = "Revisa el envío en la autorización de esta ejecución."
            return id
        } catch {
            let unknown = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
            if unknown { uncertain.insert(key) } else { pending.removeValue(forKey: scope) }
            guard current == version else { return nil }
            responseUnconfirmed = unknown
            if !unknown { pendingRequestID = nil }
            errorMessage = unknown ? "El inicio quedó sin confirmar. Sólo se consultará su identidad; no se reenviará."
                : Self.message(error)
            return nil
        }
    }
    private static func newID() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
    private static func message(_ error: Error) -> String { MaintenancePresentation.error(error) }
}
