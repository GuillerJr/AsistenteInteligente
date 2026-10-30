import Foundation
import Observation

@MainActor @Observable public final class ProductInteractionStore {
    public private(set) var scope: SessionScope?
    public private(set) var runID: String?
    public private(set) var snapshot: ProductInteractionSnapshot?
    public private(set) var isRequesting = false
    public private(set) var isChanging = false
    public private(set) var responseUnconfirmed = false
    public private(set) var errorMessage: String?
    @ObservationIgnored private var request: MaintenanceClient.Request?
    @ObservationIgnored private var version = 0
    @ObservationIgnored private var mutationVersion = 0
    @ObservationIgnored private var unconfirmed: Set<Submission> = []
    private struct Submission: Hashable {
        let scope: SessionScope; let runID: String; let kind: String; let id: String; let revision: Int
    }
    public init() {}
    public var viewIdentity: String { "\(version):\(scope?.sessionID ?? "none"):\(runID ?? "latest")" }
    public var connected: Bool { request != nil }
    public var blocksInteraction: Bool { isChanging || responseUnconfirmed || scope == nil }
    public var pendingQuestions: [ProductInteractionRecord] { rows.filter(\.mayAnswer) }
    public var panels: [ProductInteractionRecord] { rows.filter { $0.kind == "panel" && $0.status == "active" } }
    public var progress: [ProductInteractionRecord] { rows.filter { $0.kind == "progress" } }
    public var suggestions: [ProductInteractionRecord] { rows.filter { $0.kind == "suggestions" && $0.status == "active" && $0.isCurrent } }
    public var presence: ProductInteractionRecord? { rows.last { $0.kind == "presence" && $0.status == "active" && $0.isCurrent } }
    public var notifications: [ProductInteractionRecord] { rows.filter { $0.kind == "notification" } }
    private var rows: [ProductInteractionRecord] { snapshot?.interactions ?? [] }

    public func configure(_ transport: LocalRPCClient) {
        configure(request: { try await transport.call($0, params: $1) })
    }
    public func configure(request: @escaping MaintenanceClient.Request) {
        version += 1; self.request = request; scope = nil; runID = nil; snapshot = nil
        isRequesting = false; isChanging = false; responseUnconfirmed = false; errorMessage = nil
    }
    public func setScope(_ scope: SessionScope?, runID: String? = nil) {
        guard self.scope != scope || self.runID != runID else { return }
        version += 1; self.scope = scope; self.runID = runID; snapshot = nil
        isRequesting = false; isChanging = false; errorMessage = nil
        responseUnconfirmed = scope.map { scope in unconfirmed.contains { $0.scope == scope } } ?? false
    }
    public func close() {
        version += 1; request = nil; scope = nil; runID = nil; snapshot = nil
        isRequesting = false; isChanging = false; responseUnconfirmed = false; errorMessage = nil
    }
    public func refresh() async {
        guard let request, let scope, !isRequesting, !isChanging else { return }
        let current = version, mutation = mutationVersion, requestedRunID = runID
        isRequesting = true
        defer { if version == current { isRequesting = false } }
        do {
            var params = scope.params
            if let requestedRunID { params["run_id"] = .string(requestedRunID) }
            let response = try await request("product.snapshot", params)
            let observed = try ProductInteractionSnapshot(response, expectedScope: scope, expectedRunID: requestedRunID)
            guard current == version, mutation == mutationVersion, !Task.isCancelled else { return }
            if let previous = snapshot?.binding, previous != observed.binding {
                // Profile/workspace/agent/generation or latest-run movement is a
                // new UI identity even when the sidebar selection did not move.
                version += 1; isRequesting = false
            }
            snapshot = observed
            if !responseUnconfirmed { errorMessage = nil }
        } catch {
            if current == version, mutation == mutationVersion, !Task.isCancelled {
                snapshot = nil; errorMessage = Self.message(error)
            }
        }
    }
    /// Attach to a visible chat/panel .task(id: viewIdentity). Only reads are polled.
    public func pollWhileVisible() async {
        let current = version
        while current == version, !Task.isCancelled {
            await refresh()
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
        }
    }
    /// Retire only the warning after reading current state. The exact uncertain
    /// submission stays in the ledger and can never be sent again by this store.
    public func acknowledgeUnconfirmed() {
        guard !isChanging, snapshot != nil else { return }
        responseUnconfirmed = false
        errorMessage = "Se conserva el envío anterior sin confirmar. No se reenviará."
    }

    private func owns(_ row: ProductInteractionRecord) -> Bool {
        snapshot?.interactions.contains(where: { $0.id == row.id && $0.revision == row.revision
            && $0.scopeRunID == row.scopeRunID && $0.kind == row.kind && $0.status == row.status
            && $0.binding == row.binding }) == true
    }
    private func perform(_ kind: String, arguments: [String: RPCValue], runID: String,
                         id: String, revision: Int, refreshAfter: Bool = true,
                         validate: (RPCValue) throws -> Void) async -> RPCValue? {
        guard let request, let scope, !blocksInteraction, !Task.isCancelled else { return nil }
        let submission = Submission(scope: scope, runID: runID, kind: kind, id: id, revision: revision)
        guard !unconfirmed.contains(submission) else {
            responseUnconfirmed = true; errorMessage = "Este envío quedó sin confirmar y no se repetirá."; return nil
        }
        var params = scope.params
        params["run_id"] = .string(runID); params["kind"] = .string(kind); params["arguments"] = .object(arguments)
        let current = version
        mutationVersion += 1; isChanging = true; errorMessage = nil
        defer { if current == version { isChanging = false } }
        do {
            let response = try await request("product.interaction", params)
            let result = response["result"]
            try ProductInteractionValidation.bounded(result)
            guard result.object != nil else { throw RPCFailure.invalidResponse }
            try validate(result)
            guard current == version, !Task.isCancelled else { return nil }
            isChanging = false
            if refreshAfter { await refresh() }
            guard current == version, !Task.isCancelled else { return nil }
            return result
        } catch {
            if MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error) { unconfirmed.insert(submission) }
            guard current == version else { return nil }
            responseUnconfirmed = unconfirmed.contains(submission)
            errorMessage = responseUnconfirmed
                ? "El envío quedó sin confirmar. Consulta el estado; Jarvis no lo repetirá."
                : Self.message(error)
            return nil
        }
    }

    public func answer(_ row: ProductInteractionRecord, answers: [String: ProductQuestionAnswer]) async -> Bool {
        guard owns(row), row.mayAnswer, Set(answers.keys) == Set(row.questions.map(\.id)),
              row.questions.allSatisfy({ answers[$0.id]?.valid(for: $0) == true }) else { return false }
        let result = await perform("question.answer", arguments: ["id": .string(row.id),
            "revision": .number(Double(row.revision)), "answers": .object(answers.mapValues(\.value))],
            runID: row.scopeRunID, id: row.id, revision: row.revision) { value in
                guard value["id"].string == row.id, value["revision"].integer == row.revision + 1,
                      value["status"].string == "answered", value["grants_tool_approval"].bool == false else {
                    throw RPCFailure.invalidResponse
                }
            }
        return result != nil
    }
    public func cancelQuestion(_ row: ProductInteractionRecord) async {
        guard owns(row), row.mayAnswer else { return }
        _ = await perform("question.cancel", arguments: ["id": .string(row.id), "revision": .number(Double(row.revision))],
            runID: row.scopeRunID, id: row.id, revision: row.revision) { value in
                guard value["id"].string == row.id, value["status"].string == "cancelled",
                      value["revision"].integer == row.revision + 1 else { throw RPCFailure.invalidResponse }
            }
    }
    public func setPanel(_ panel: ProductCanvasDefinition, revision: Int, runID: String = "host",
                         expectedIdentity: String? = nil) async -> Bool {
        if let expectedIdentity, expectedIdentity != viewIdentity { return false }
        guard revision >= 0, (try? ProductCanvasDefinition(panel.submission)) != nil else { return false }
        let result = await perform("panel.set", arguments: ["revision": .number(Double(revision)), "panel": panel.submission],
            runID: runID, id: "panel", revision: revision) { value in
                guard value["kind"].string == "panel", value["revision"].integer == revision + 1 else { throw RPCFailure.invalidResponse }
                _ = try ProductCanvasDefinition(value["payload"])
            }
        return result != nil
    }
    public func toggle(_ row: ProductInteractionRecord, widgetID: String, itemIndex: Int, checked: Bool) async {
        guard owns(row), row.status == "active", let widget = row.panel?.widgets.first(where: { $0.id == widgetID }),
              widget.type == "checklist", widget.items.contains(where: { $0.index == itemIndex }) else { return }
        _ = await perform("panel.toggle", arguments: ["id": .string(row.id), "revision": .number(Double(row.revision)),
            "widget_id": .string(widgetID), "item_index": .number(Double(itemIndex)), "checked": .bool(checked)],
            runID: row.scopeRunID, id: row.id, revision: row.revision) { value in
                guard value["id"].string == row.id, value["revision"].integer == row.revision + 1 else { throw RPCFailure.invalidResponse }
                _ = try ProductCanvasDefinition(value["payload"])
            }
    }
    public func selectSuggestion(_ row: ProductInteractionRecord, suggestionID: String) async -> String? {
        guard owns(row), row.status == "active", row.isCurrent, row.suggestions.contains(where: { $0.id == suggestionID }) else { return nil }
        let result = await perform("suggestion.select", arguments: ["id": .string(row.id),
            "revision": .number(Double(row.revision)), "suggestion_id": .string(suggestionID)],
            runID: row.scopeRunID, id: row.id, revision: row.revision) { value in
                guard value["execution"].string == "draft_only", value["grants_tool_approval"].bool == false else { throw RPCFailure.invalidResponse }
                _ = try ProductInteractionValidation.text(value["draft_text"], limit: 2048)
            }
        return result?["draft_text"].string
    }
    public func dismissSuggestions(_ row: ProductInteractionRecord) async -> Bool {
        guard owns(row), row.kind == "suggestions", row.status == "active", row.isCurrent else { return false }
        let result = await perform("suggestion.dismiss", arguments: ["id": .string(row.id),
            "revision": .number(Double(row.revision))], runID: row.scopeRunID,
            id: row.id, revision: row.revision) { value in
                guard value["id"].string == row.id, value["kind"].string == "suggestions",
                      value["status"].string == "cancelled", value["revision"].integer == row.revision + 1,
                      value["payload"]["execution"].string == "draft_only" else { throw RPCFailure.invalidResponse }
            }
        return result != nil
    }
    public func configureNotifications(enabled: Bool, silenced: Bool, expectedIdentity: String? = nil) async {
        if let expectedIdentity, expectedIdentity != viewIdentity { return }
        guard let preferences = snapshot?.notificationPreferences else { return }
        _ = await perform("notifications.configure", arguments: ["revision": .number(Double(preferences.revision)),
            "enabled": .bool(enabled), "silenced": .bool(silenced)], runID: "host", id: "preferences", revision: preferences.revision) { value in
                let observed = try ProductNotificationPreferences(value)
                guard observed.revision == preferences.revision + 1, observed.enabled == enabled, observed.silenced == silenced else { throw RPCFailure.invalidResponse }
            }
    }
    public func claimNotification(_ row: ProductInteractionRecord) async -> ProductNotificationClaim? {
        guard let scope, owns(row), row.kind == "notification", row.status == "queued", row.isCurrent else { return nil }
        let result = await perform("notification.claim", arguments: ["id": .string(row.id), "revision": .number(Double(row.revision))],
            runID: row.scopeRunID, id: row.id, revision: row.revision) { value in
                let claim = try ProductNotificationClaim(value, scope: scope, runID: row.scopeRunID, id: row.id)
                guard claim.revision == row.revision + 1 else { throw RPCFailure.invalidResponse }
            }
        return result.flatMap { try? ProductNotificationClaim($0, scope: scope, runID: row.scopeRunID, id: row.id) }
    }
    public func validateNotification(_ claim: ProductNotificationClaim) async -> Bool {
        guard scope == claim.scope else { return false }
        return await perform("notification.validate", arguments: ["id": .string(claim.id), "revision": .number(Double(claim.revision)),
            "claim": .string(claim.claim)], runID: claim.runID, id: claim.id,
            revision: claim.revision, refreshAfter: false) { value in
                guard value["id"].string == claim.id, value["revision"].integer == claim.revision, value["valid"].bool == true else { throw RPCFailure.invalidResponse }
            } != nil
    }
    public func settleNotification(_ claim: ProductNotificationClaim, outcome: String) async {
        guard scope == claim.scope, ["delivered", "failed", "unknown"].contains(outcome) else { return }
        _ = await perform("notification.settle", arguments: ["id": .string(claim.id), "revision": .number(Double(claim.revision)),
            "claim": .string(claim.claim), "outcome": .string(outcome)], runID: claim.runID, id: claim.id, revision: claim.revision) { value in
                guard value["id"].string == claim.id, value["revision"].integer == claim.revision + 1,
                      value["status"].string == outcome else { throw RPCFailure.invalidResponse }
            }
    }
    public func resumeNotification(_ row: ProductInteractionRecord) async -> ProductNotificationNavigation? {
        guard let scope, owns(row), row.kind == "notification", row.isCurrent,
              ["queued", "delivered", "failed", "unknown"].contains(row.status) else { return nil }
        let result = await perform("notification.resume", arguments: ["id": .string(row.id), "revision": .number(Double(row.revision))],
            runID: row.scopeRunID, id: row.id, revision: row.revision) { value in
                guard value["id"].string == row.id, value["revision"].integer == row.revision + 1,
                      row.attentionKind != "question" || value["question_id"].string == row.correlationID else {
                    throw RPCFailure.invalidResponse
                }
                _ = try ProductNotificationNavigation(value, scope: scope, runID: row.scopeRunID)
            }
        return result.flatMap { try? ProductNotificationNavigation($0, scope: scope, runID: row.scopeRunID) }
    }
    private static func message(_ error: Error) -> String {
        if let failure = error as? RPCFailure, case .remote = failure {
            return "El estado o los permisos cambiaron. Consulta la actividad antes de continuar."
        }
        return "No se pudo consultar la actividad local. Comprueba la conexión y actualiza el estado."
    }
}
