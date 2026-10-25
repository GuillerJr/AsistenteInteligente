import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class ChatStore {
    var sessions: [ChatSession] = []
    var selectedID: String?
    var messages: [ChatMessage] = []
    var activities: [RunActivity] = []
    var receipts: [ActionReceipt] = []
    private var drafts = SessionDrafts()
    var draft: String {
        get { drafts.value(for: selected?.scope) }
        set { drafts.set(newValue, for: selected?.scope) }
    }
    var streamingText = ""
    var errorMessage: String?
    var statusText = "Iniciando servicio local…"
    var connected = false
    var ready = false
    var busy = false
    var starting = false
    var activeRunID: String?
    var runPhase = ""
    var capabilities: RPCValue = .null
    let access = AccessStore()
    let mcp = MCPStore()
    let management = ManagementStore()
    var newAgentID = "jarvis"
    var lostTemporaryRun = false

    @ObservationIgnored private var client: LocalRPCClient?
    @ObservationIgnored private let service = ServiceProcess()
    @ObservationIgnored private var monitor: Task<Void, Never>?
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var started = false
    @ObservationIgnored private var options: LaunchOptions?
    @ObservationIgnored private var pendingSubmission: PendingSubmission?
    @ObservationIgnored private var activeSession: ChatSession?
    @ObservationIgnored private var stopping = false

    var selected: ChatSession? { sessions.first { $0.id == selectedID } }
    var canSend: Bool { ready && connected && !busy && !access.isChangingWorkspace && !mcp.isChanging && !management.isChanging && activeRunID == nil && selected != nil
                       && selected?.archived == false
                       && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var canChangeSession: Bool { ready && !busy && !access.isChangingWorkspace && !mcp.isChanging && !management.isChanging && activeRunID == nil }
    var selectedAgentName: String { management.agents.first(where: { $0.id == selected?.agentID })?.name ?? selected?.agentID ?? "Jarvis" }

    func start() async {
        guard !started else { return }
        started = true
        starting = true
        defer { starting = false }
        do {
            let options = try LaunchOptions.parse(Array(CommandLine.arguments.dropFirst()))
            self.options = options
            let connection = LocalRPCClient(stateDirectory: options.state)
            client = connection
            access.configure(client: LocalRPCClient(stateDirectory: options.state))
            mcp.configure(client: LocalRPCClient(stateDirectory: options.state))
            management.configure(client: LocalRPCClient(stateDirectory: options.state))
            try service.start(options)
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try await refreshStatus()
            try await management.refresh()
            try await refreshSessions()
            if sessions.isEmpty { await createSession(temporary: false) }
            else if let first = sessions.first { await selectSession(first.id) }
            try await access.refreshCatalog()
            try await mcp.refresh()
            startMonitor()
        } catch {
            statusText = "Jarvis necesita configuración"
            report(error)
        }
    }

    private func startMonitor() {
        guard !stopping else { return }
        monitor?.cancel()
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
                guard let self else { return }
                if self.starting { continue }
                do {
                    guard self.service.isRunning else { throw RPCFailure.disconnected }
                    try await self.refreshStatus()
                    try await self.access.refreshCatalog()
                    try await self.mcp.refresh()
                    try await self.management.refresh()
                    if self.canChangeSession { try await self.refreshSessions() }
                    if self.activeRunID == nil { try await self.access.refreshApprovals(runID: nil) }
                    if self.runPhase == "disconnected", self.activeRunID != nil { self.resumeStatus() }
                } catch {
                    if Task.isCancelled || self.stopping { return }
                    self.ready = false; self.connected = false
                    self.statusText = "Servicio desconectado"
                    self.report(error)
                    // Only read-side supervision retries. Mutations keep their identities.
                }
            }
        }
    }

    private func rpc(_ method: String, _ params: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard !stopping else { throw CancellationError() }
        guard let client else { throw RPCFailure.configuration }
        return try await client.call(method, params: params)
    }

    private func refreshStatus() async throws {
        let result = try await rpc("service.status")
        guard !stopping else { throw CancellationError() }
        guard let serverReady = result["ready"].bool,
              let accountConnected = result["provider"]["connected"].bool else {
            throw RPCFailure.invalidResponse
        }
        ready = serverReady
        connected = accountConnected
        capabilities = result["capabilities"]
        access.refreshCapabilities(capabilities)
        statusText = accountConnected ? "Codex con ChatGPT conectado" : "Inicia sesión con ChatGPT"
    }

    private func refreshSessions() async throws {
        var persistent: [ChatSession] = []
        var offset = 0
        var complete = false
        for _ in 0..<50 {
            let result = try await rpc("sessions.list", ["offset": .number(Double(offset)),
                                                         "include_archived": .bool(true)])
            guard let list = result["sessions"].array else { throw RPCFailure.invalidResponse }
            let page = list.compactMap(ChatSession.init)
            guard page.count == list.count else { throw RPCFailure.invalidResponse }
            persistent += page
            if result["has_more"].bool != true { complete = true; break }
            guard let next = result["next_offset"].integer, next > offset else {
                throw RPCFailure.invalidResponse
            }
            offset = next
        }
        guard complete else { throw RPCFailure.oversized }
        let temporary = sessions.filter(\.temporary)
        sessions = persistent + temporary.filter { candidate in !persistent.contains { $0.id == candidate.id } }
    }

    func createSession(temporary: Bool) async {
        guard canChangeSession else { return }
        guard management.agents.contains(where: { $0.id == newAgentID }) else {
            errorMessage = "Selecciona un agente disponible para la conversación nueva."; return
        }
        busy = true
        defer { busy = false }
        do {
            let result = try await rpc("sessions.create", ["title": .string(temporary ? "Chat temporal" : "Nueva conversación"),
                                                            "temporary": .bool(temporary), "agent_id": .string(newAgentID)])
            guard let session = ChatSession(result["session"]) else { throw RPCFailure.invalidResponse }
            sessions.insert(session, at: 0)
            selectedID = session.id
            access.setContext(session.scope)
            management.setContext(session.scope)
            messages = []; activities = []; draft = ""; streamingText = ""; errorMessage = nil
            receipts = []
        } catch { report(error) }
    }

    func selectSession(_ id: String) async {
        guard canChangeSession, let session = sessions.first(where: { $0.id == id }) else { return }
        busy = true
        defer { busy = false }
        do {
            let rows = try await fetchMessages(session)
            selectedID = id
            access.setContext(session.scope)
            management.setContext(session.scope)
            messages = try decodeMessages(rows)
            activities = []; streamingText = ""; errorMessage = nil
            receipts = []
            try await access.refreshApprovals(runID: nil)
            try await recoverLatestRun(session)
        } catch { report(error) }
    }

    private func scope(_ session: ChatSession) -> [String: RPCValue] {
        ["session_id": .string(session.id), "temporary": .bool(session.temporary)]
    }

    private func decodeMessages(_ rows: [RPCValue]) throws -> [ChatMessage] {
        try rows.map { row in
            guard let role = row["role"].string, let text = row["text"].string else {
                throw RPCFailure.invalidResponse
            }
            return ChatMessage(role: role, text: text)
        }
    }

    private func refreshMessages(_ session: ChatSession) async throws {
        let rows = try await fetchMessages(session)
        if selectedID == session.id { messages = try decodeMessages(rows) }
    }

    private func fetchMessages(_ session: ChatSession) async throws -> [RPCValue] {
        var rows: [RPCValue] = []
        var after = 0
        for _ in 0..<50 {
            var params = scope(session)
            params["after"] = .number(Double(after))
            let result = try await rpc("sessions.messages", params)
            guard let page = result["messages"].array else { throw RPCFailure.invalidResponse }
            rows += page
            if result["has_more"].bool != true { return rows }
            guard let next = result["next_after"].integer, next > after else {
                throw RPCFailure.invalidResponse
            }
            after = next
        }
        throw RPCFailure.oversized
    }

    func send() async {
        guard canSend, let session = selected else { return }
        busy = true
        defer { busy = false }
        var params = scope(session)
        params["message"] = .string(draft)
        let requestID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        params["request_id"] = .string(requestID)
        let submission = PendingSubmission(id: requestID, session: session, text: draft)
        pendingSubmission = submission
        activeSession = session
        errorMessage = nil
        streamingText = ""
        do {
            let result = try await rpc("runs.start", params)
            guard let runID = result["run_id"].string else { throw RPCFailure.invalidResponse }
            activeRunID = runID
            runPhase = result["status"].string ?? "queued"
            if draft == submission.text { draft = "" }
            activities = []
            receipts = []
            // A confirmed run identity starts polling even if refreshing history fails.
            watchRun(runID, session: session)
            try await refreshMessages(session)
        } catch {
            report(error)
            if RunPresentation.startWasRejected(error) { pendingSubmission = nil; activeSession = nil; return }
            // A missing acknowledgement is not proof that the server did not start.
            activeRunID = requestID
            runPhase = "disconnected"
        }
    }

    private func watchRun(_ runID: String, session: ChatSession) {
        polling?.cancel()
        polling = Task { [weak self] in
            guard let self else { return }
            var after = 0
            var params = self.scope(session)
            params["run_id"] = .string(runID)
            while !Task.isCancelled {
                do {
                    var eventParams = params
                    eventParams["after"] = .number(Double(after))
                    let events = try await self.rpc("events.poll", eventParams)
                    if let rows = events["events"].array {
                        for row in rows {
                            if let sequence = row["sequence"].integer, sequence > after,
                               let kind = row["kind"].string {
                                self.activities.append(RunActivity(id: sequence, kind: kind))
                                if self.activities.count > 500 { self.activities.removeFirst() }
                                if kind == "answer.delta", let text = row["payload"]["text"].string {
                                    self.streamingText = try RunPresentation.appendDelta(text, to: self.streamingText)
                                }
                                after = sequence
                            }
                        }
                    }
                    let result = try await self.rpc("runs.get", params)
                    try await self.access.refreshApprovals(runID: runID)
                    guard let phase = result["run"]["status"].string else {
                        throw RPCFailure.invalidResponse
                    }
                    self.runPhase = phase
                    try self.applyReceipts(result["run"])
                    if ["completed", "failed", "cancelled", "interrupted"].contains(phase) {
                        try await self.refreshMessages(session)
                        if let error = result["run"]["error"].string, !error.isEmpty {
                            self.errorMessage = RunPresentation.errorMessage(error)
                        }
                        self.streamingText = ""
                        self.activeRunID = nil
                        self.pendingSubmission = nil
                        self.activeSession = nil
                        return
                    }
                    try await Task.sleep(for: .milliseconds(350))
                } catch is CancellationError { return }
                catch {
                    if Task.isCancelled || self.stopping { return }
                    self.report(error)
                    // Preserve the run identity: connection failure is not settlement.
                    self.runPhase = "disconnected"
                    return
                }
            }
        }
    }

    func cancel() async {
        guard let runID = activeRunID, let session = activeSession ?? selected else { return }
        var params = scope(session); params["run_id"] = .string(runID)
        do { _ = try await rpc("runs.cancel", params); runPhase = "cancelling" }
        catch { report(error) }
    }

    func resumeStatus() {
        guard runPhase != "checking" else { return }
        guard let runID = activeRunID, let session = activeSession ?? selected else { return }
        runPhase = "checking"
        watchRun(runID, session: session)
    }

    private func recoverLatestRun(_ session: ChatSession) async throws {
        var offset = 0
        var latest: RPCValue?
        var complete = false
        for _ in 0..<20 {
            var parameters = scope(session)
            parameters["offset"] = .number(Double(offset))
            let result = try await rpc("runs.list", parameters)
            guard let rows = result["runs"].array, let more = result["has_more"].bool,
                  let next = result["next_offset"].integer else { throw RPCFailure.invalidResponse }
            latest = rows.last ?? latest
            if !more { complete = true; break }
            guard next > offset else { throw RPCFailure.invalidResponse }
            offset = next
        }
        guard complete else { throw RPCFailure.oversized }
        guard let latest, let runID = latest["run_id"].string,
              let phase = latest["status"].string else { return }
        var parameters = scope(session); parameters["run_id"] = .string(runID)
        let result = try await rpc("runs.get", parameters)
        try applyReceipts(result["run"])
        runPhase = phase
        if ["queued", "running", "waiting_approval", "cancelling", "cleaning"].contains(phase) {
            activeRunID = runID
            activeSession = session
            watchRun(runID, session: session)
        } else if latest["outcome"].string == "unknown" {
            errorMessage = "El resultado de una ejecución anterior quedó sin confirmar. No se repetirá automáticamente."
        }
    }

    private func applyReceipts(_ run: RPCValue) throws {
        guard let values = run["action_receipts"].array else { throw RPCFailure.invalidResponse }
        let parsed = try values.map(ActionReceipt.init)
        guard Set(parsed.map(\.id)).count == parsed.count else { throw RPCFailure.invalidResponse }
        receipts = parsed
    }

    func reconnect() async {
        guard !starting, let options else { return }
        starting = true
        defer { starting = false; startMonitor() }
        polling?.cancel()
        await client?.close()
        await access.close()
        await mcp.close()
        await management.close()
        do {
            let restarted = !service.isRunning
            if restarted {
                _ = await service.stop()
                try service.start(options)
                drafts.clearTemporary()
                sessions.removeAll(where: \.temporary)
                access.setContext(nil)
                management.setContext(nil)
            }
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try await refreshStatus()
            try await refreshSessions()
            try await access.refreshCatalog()
            try await mcp.refresh()
            try await management.refresh()
            if restarted, activeSession?.temporary == true {
                lostTemporaryRun = true
                runPhase = "disconnected"
                errorMessage = "El chat temporal se perdió al reiniciar el servicio. No se puede confirmar el resultado de su ejecución y no se repetirá."
            } else if activeRunID != nil {
                access.setContext((activeSession ?? selected)?.scope)
                management.setContext((activeSession ?? selected)?.scope)
                runPhase = "disconnected"
                resumeStatus()
            }
            else if let id = selectedID, sessions.contains(where: { $0.id == id }) { await selectSession(id) }
            else if let first = sessions.first { await selectSession(first.id) }
            else { await createSession(temporary: false) }
        } catch { report(error) }
    }

    func acknowledgeLostTemporaryRun() {
        guard lostTemporaryRun else { return }
        lostTemporaryRun = false; activeRunID = nil; pendingSubmission = nil; activeSession = nil
        selectedID = nil; messages = []; streamingText = ""; activities = []; receipts = []
        access.setContext(nil)
        management.setContext(nil)
        runPhase = "interrupted"
    }

    func renameSession(_ session: ChatSession, title: String) async -> Bool {
        guard canChangeSession else { return false }
        var parameters = scope(session); parameters["title"] = .string(title)
        return await updateSession(parameters)
    }

    func forkSession(_ session: ChatSession) async {
        guard canChangeSession,
              OwnerManagementReview.confirm(title: "Ramificar conversación", action: "Crear rama",
                detail: "Se copiará únicamente el contexto completado de «\(session.title)» a una conversación nueva del mismo agente. No se ejecutan ni copian acciones, runs o autorizaciones.") else { return }
        busy = true
        do {
            let result = try await rpc("sessions.fork", scope(session))
            guard let created = ChatSession(result["session"]), created.parentSessionID == session.id,
                  created.agentID == session.agentID, created.temporary == session.temporary else { throw RPCFailure.invalidResponse }
            try await refreshSessions()
            if !sessions.contains(where: { $0.id == created.id }) { sessions.insert(created, at: 0) }
            busy = false
            await selectSession(created.id)
        } catch { busy = false; report(error) }
    }

    func openScheduledSession(_ id: String) async {
        guard canChangeSession else { return }
        do { try await refreshSessions(); await selectSession(id) } catch { report(error) }
    }

    func prepareProcessStop(_ process: ProcessSnapshot) {
        guard canChangeSession, draft.isEmpty, process.running else {
            errorMessage = "Termina la ejecución o conserva primero el borrador actual antes de preparar otra solicitud."; return
        }
        draft = "Detén únicamente el proceso \(process.id) con process_stop. Espera mi aprobación de esa operación."
    }

    func setArchived(_ session: ChatSession, archived: Bool) async {
        guard canChangeSession else { return }
        var parameters = scope(session); parameters["archived"] = .bool(archived)
        _ = await updateSession(parameters)
    }

    private func updateSession(_ parameters: [String: RPCValue]) async -> Bool {
        busy = true
        defer { busy = false }
        do {
            let result = try await rpc("sessions.update", parameters)
            guard let value = ChatSession(result["session"]),
                  let index = sessions.firstIndex(where: { $0.id == value.id }) else {
                throw RPCFailure.invalidResponse
            }
            sessions[index] = value
            return true
        } catch { report(error); return false }
    }

    func deleteSession(_ session: ChatSession) async {
        guard canChangeSession else { return }
        busy = true
        do {
            let result = try await rpc("sessions.delete", scope(session))
            guard result["deleted"].bool == true else { throw RPCFailure.invalidResponse }
            sessions.removeAll(where: { $0.id == session.id })
            drafts.remove(session.scope)
            if selectedID == session.id {
                selectedID = nil; messages = []; activities = []; streamingText = ""; receipts = []
                access.setContext(nil)
                management.setContext(nil)
            }
        } catch { report(error) }
        busy = false
        if selectedID == nil, let first = sessions.first { await selectSession(first.id) }
    }

    func login() async {
        do {
            let result = try await rpc("provider.login")
            guard let raw = result["url"].string, let url = URL(string: raw), url.scheme == "https",
                  url.host == "auth.openai.com", url.user == nil, url.password == nil,
                  url.port == nil || url.port == 443 else {
                throw RPCFailure.invalidResponse
            }
            guard NSWorkspace.shared.open(url) else { throw RPCFailure.unavailable }
        } catch { report(error) }
    }

    func logout() async {
        guard activeRunID == nil else { return }
        do { _ = try await rpc("provider.logout"); try await refreshStatus() }
        catch { report(error) }
    }

    func shutdown() async -> Bool {
        stopping = true
        ready = false
        statusText = "Cerrando el servicio y asentando las ejecuciones…"
        monitor?.cancel(); polling?.cancel()
        access.setContext(nil)
        management.setContext(nil)
        // Stopping the owned service wakes blocked RPC readers. Waiting for a
        // socket queue before sending SIGTERM could otherwise delay shutdown.
        let graceful = await service.stop()
        await client?.close()
        await access.close()
        await mcp.close()
        await management.close()
        return graceful
    }

    private func report(_ error: Error) {
        errorMessage = CapabilityPresentation.errorMessage(error)
    }
}
