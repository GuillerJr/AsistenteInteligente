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
    let telegram = TelegramStore()
    let maintenance = MaintenanceStore()
    let restored = RestoreProfileStore()
    let runtime: ProfileRuntimeGuard
    var localProfileStatus: LocalProfileStatus?
    var handoffPreparing = false
    @ObservationIgnored private let suppliedOptions: LaunchOptions?
    @ObservationIgnored private let openTarget: ProfileOpenTarget?
    let voice: LocalVoiceController
    var dictationNotice: String?
    var newAgentID = "jarvis"
    var lostTemporaryRun = false

    @ObservationIgnored private var client: LocalRPCClient?
    @ObservationIgnored private let service = ServiceProcess()
    @ObservationIgnored private var monitor: Task<Void, Never>?
    @ObservationIgnored private var monitorReadActive = false
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var started = false
    @ObservationIgnored private var options: LaunchOptions?
    @ObservationIgnored private var pendingSubmission: PendingSubmission?
    @ObservationIgnored private var activeSession: ChatSession?
    @ObservationIgnored private var stopping = false
    @ObservationIgnored private var awaitingStartupReadiness = false
    @ObservationIgnored private var startupErrorMessage: String?
    @ObservationIgnored private var conversationRefresh: ConversationRefresh?
    @ObservationIgnored private var observedConversationRunID: String?

    var isShuttingDown: Bool { stopping }
    var launchOptions: LaunchOptions? { options }
    var serviceOwner: ServiceOwnerIdentity? { service.ownedIdentity }
    var durableDraftSnapshot: SessionDrafts { drafts.durableOnly }
    var mayHandoff: Bool { ready && !starting && !stopping && !handoffPreparing && !busy && activeRunID == nil && !voice.canStopDictation && !access.isChangingWorkspace && !mcp.isChanging && !management.isChanging && !telegram.isChanging && !maintenance.blocksInteraction && !restored.isRequesting && !restored.responseUnconfirmed && restored.operation?.workerPending != true && restored.operation?.cleanupOwnerPending != true }
    var shutdownWarning: String? { service.lastStopReport?.warning }

    var selected: ChatSession? { sessions.first { $0.id == selectedID } }
    var canSend: Bool { ready && connected && !busy && !access.isChangingWorkspace && !mcp.isChanging && !management.isChanging && !telegram.isChanging && !maintenance.blocksInteraction && !restored.blocksInteraction && !handoffPreparing && !voice.canStopDictation && activeRunID == nil && selected != nil
                       && selected?.archived == false
                       && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var canChangeSession: Bool { ready && !busy && !access.isChangingWorkspace && !mcp.isChanging && !management.isChanging && !telegram.isChanging && !maintenance.blocksInteraction && !restored.blocksInteraction && !handoffPreparing && !voice.canStopDictation && activeRunID == nil }
    var selectedAgentName: String { management.agents.first(where: { $0.id == selected?.agentID })?.name ?? selected?.agentID ?? "Jarvis" }
    var canUseVoice: Bool { canChangeSession && connected && selected?.archived == false && selected != nil }

    init(voice: LocalVoiceController? = nil, options: LaunchOptions? = nil, target: ProfileOpenTarget? = nil, runtime: ProfileRuntimeGuard = ProfileRuntimeGuard()) {
        self.runtime = runtime; suppliedOptions = options; openTarget = target
        self.voice = voice ?? LocalVoiceController(driver: MacLocalVoiceDriver())
        self.voice.onTranscript = { [weak self] transcript in self?.acceptDictation(transcript) }
        maintenance.waitForSupervisionIdle = { [weak self] in
            guard let self else { throw RPCFailure.configuration }
            try await self.waitForMonitorSettlement()
        }
        restored.waitForSupervisionIdle = { [weak self] in
            guard let self else { throw RPCFailure.configuration }
            try await self.waitForMonitorSettlement()
        }
    }

    func startDictation() {
        guard canUseVoice, let selected else { return }
        dictationNotice = nil
        voice.startDictation(contextID: LocalVoiceDraftBridge.contextID(selected.scope, runtimeID: runtime.id))
    }

    func readMessage(_ message: ChatMessage) {
        guard canUseVoice, message.role == "assistant", messages.contains(where: { $0.id == message.id }), let selected else { return }
        voice.play(text: message.text, contextID: LocalVoiceDraftBridge.contextID(selected.scope, runtimeID: runtime.id))
    }

    func stopVoice() { voice.cancel() }

    private func acceptDictation(_ transcript: LocalVoiceTranscript) {
        let mayAcceptDraft = ready && connected && !stopping && !handoffPreparing && selected?.archived == false && selected != nil
        guard let value = LocalVoiceDraftBridge.append(transcript, scope: selected?.scope, existing: draft, eligible: mayAcceptDraft, runtimeID: runtime.id) else {
            dictationNotice = "El dictado no se añadió: la conversación cambió o se alcanzó el límite del borrador."
            return
        }
        draft = value
        dictationNotice = transcript.completion == .partialAtStop
            ? "Se añadió un dictado parcial al borrador. Revísalo antes de enviarlo."
            : "Dictado local añadido al borrador. Revísalo antes de enviarlo."
    }

    func start() async {
        guard !started, runtime.isActive else { return }
        started = true
        starting = true
        defer { starting = false }
        do {
            let options = try suppliedOptions ?? LaunchOptions.parse(Array(CommandLine.arguments.dropFirst()))
            self.options = options
            let connection = LocalRPCClient(stateDirectory: options.state, runtime: runtime)
            client = connection
            conversationRefresh = ConversationRefresh(reader: ConversationReader(transport: connection))
            access.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
            mcp.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
            management.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
            telegram.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
            maintenance.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
            restored.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
            try service.start(options)
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try await refreshStatus()
            try await management.refresh()
            try await refreshSessions()
            if sessions.isEmpty, options.expectedRestore == nil { await createSession(temporary: false) }
            else if let first = sessions.first { await selectSession(first.id) }
            try await access.refreshCatalog()
            try await mcp.refresh()
            startMonitor()
        } catch {
            if openTarget != nil, error as? RPCFailure == .invalidResponse {
                _ = await shutdown()
                statusText = "El nuevo perfil no coincide con el destino revisado"
                report(error)
                return
            }
            if recordStartupFailure(error) { startMonitor() }
            else { statusText = "Jarvis necesita configuración" }
        }
    }

    private func startMonitor() {
        guard !stopping else { return }
        monitor?.cancel()
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
                guard let self else { return }
                if self.starting || self.maintenance.monitorPaused || self.restored.monitorPaused || self.handoffPreparing { continue }
                do {
                    try await self.refreshMonitoredState()
                } catch {
                    if Task.isCancelled || self.stopping { return }
                    if MaintenanceMonitorPolicy.isTemporaryBusy(error) { continue }
                    self.ready = false; self.connected = false
                    self.voice.cancel()
                    if self.awaitingStartupReadiness, self.service.isRunning,
                       let failure = error as? RPCFailure,
                       [.startupUnconfirmed, .unavailable, .disconnected].contains(failure) {
                        self.reportPendingStartup()
                    } else {
                        self.awaitingStartupReadiness = false
                        self.statusText = "Servicio desconectado"
                        self.report(error)
                    }
                    // Only read-side supervision retries. Mutations keep their identities.
                }
            }
        }
    }

    private func waitForMonitorSettlement() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(30))
        while monitorReadActive {
            try Task.checkCancellation()
            guard !stopping, ContinuousClock.now < deadline else { throw RPCFailure.unavailable }
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    private func refreshMonitoredState() async throws {
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        monitorReadActive = true
        defer { monitorReadActive = false }
        guard service.isRunning else { throw RPCFailure.disconnected }
        if awaitingStartupReadiness, let options = options {
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state, timeout: 1) { service.isRunning }
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        }
        try await refreshStatus()
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await access.refreshCatalog()
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await mcp.refresh()
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await management.refresh()
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        if canChangeSession { try await refreshSessions() }
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await refreshSelectedConversation()
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        if activeRunID == nil { try await access.refreshApprovals(runID: nil) }
        guard !maintenance.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        if runPhase == "disconnected", activeRunID != nil { resumeStatus() }
        // Recovery performs only reads. It never selects/creates a
        // conversation, relaunches a child, or resends an operation.
        finishStartupRecovery()
    }

    private func rpc(_ method: String, _ params: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard !stopping, !handoffPreparing || method == "service.local_status" else { throw CancellationError() }
        try runtime.check()
        guard let client else { throw RPCFailure.configuration }
        let result = try await client.call(method, params: params)
        try runtime.check()
        return result
    }

    func readLocalStatus() async throws -> LocalProfileStatus {
        let value = try await LocalProfileStatus(rpc("service.local_status"))
        if let target = openTarget, !value.matches(target) { throw RPCFailure.invalidResponse }
        return value
    }

    private func refreshStatus() async throws {
        let local = try await readLocalStatus()
        localProfileStatus = local
        // The host returns local capabilities without touching an SDK while
        // provider choice is absent; integration tests require zero calls.
        let result = try await rpc("service.status")
        guard !stopping else { throw CancellationError() }
        guard let serverReady = result["ready"].bool,
              let accountConnected = result["provider"]["connected"].bool else {
            throw RPCFailure.invalidResponse
        }
        guard local.providerQueryAllowed || !accountConnected else { throw RPCFailure.invalidResponse }
        ready = serverReady
        connected = local.providerQueryAllowed && accountConnected
        if !serverReady || !accountConnected { voice.cancel() }
        capabilities = result["capabilities"]
        access.refreshCapabilities(capabilities)
        statusText = !local.providerQueryAllowed ? "Perfil local abierto · elige conectar tu cuenta de ChatGPT" : accountConnected ? "Codex con ChatGPT conectado" : "Inicia sesión con ChatGPT"
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
        voice.cancel(); dictationNotice = nil
        guard management.agents.contains(where: { $0.id == newAgentID }) else {
            errorMessage = "Selecciona un agente disponible para la conversación nueva."; return
        }
        conversationRefresh?.setScope(nil)
        busy = true
        defer { busy = false; conversationRefresh?.setScope(selected?.scope) }
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
            observedConversationRunID = nil
        } catch { report(error) }
    }

    func selectSession(_ id: String) async {
        guard canChangeSession, let session = sessions.first(where: { $0.id == id }) else { return }
        conversationRefresh?.setScope(nil)
        voice.cancel(); dictationNotice = nil
        busy = true
        defer { busy = false; conversationRefresh?.setScope(selected?.scope) }
        do {
            let rows = try await fetchMessages(session)
            selectedID = id
            access.setContext(session.scope)
            management.setContext(session.scope)
            messages = try decodeMessages(rows, scope: session.scope)
            activities = []; streamingText = ""; errorMessage = nil
            receipts = []
            try await access.refreshApprovals(runID: nil)
            try await recoverLatestRun(session)
        } catch { report(error) }
    }

    private func scope(_ session: ChatSession) -> [String: RPCValue] {
        ["session_id": .string(session.id), "temporary": .bool(session.temporary)]
    }

    private func decodeMessages(_ rows: [RPCValue], scope: SessionScope) throws -> [ChatMessage] {
        try rows.map { row in
            let message = try ConversationMessage(row, scope: scope)
            return chatMessage(message, scope: scope)
        }
    }

    private func chatMessage(_ message: ConversationMessage, scope: SessionScope) -> ChatMessage {
        ChatMessage(id: "\(scope.temporary):\(scope.sessionID):\(message.id)", role: message.role, text: message.text)
    }

    private func refreshMessages(_ session: ChatSession) async throws {
        let rows = try await fetchMessages(session)
        if selected?.scope == session.scope {
            let decoded = try decodeMessages(rows, scope: session.scope)
            if messages != decoded { messages = decoded }
        }
    }

    private func refreshSelectedConversation() async throws {
        guard !busy, !starting, activeRunID == nil, let session = selected,
              let snapshot = try await conversationRefresh?.refresh() else { return }
        guard !stopping, !Task.isCancelled, !busy, activeRunID == nil, selected?.scope == snapshot.scope else { return }
        let decoded = snapshot.messages.map { chatMessage($0, scope: snapshot.scope) }
        if messages != decoded { messages = decoded }
        guard let run = snapshot.latestRun else { return }
        if receipts != run.receipts { receipts = run.receipts }
        let newRun = observedConversationRunID != run.id
        observedConversationRunID = run.id
        if run.active {
            // An external channel owns the turn; this window only follows its
            // existing run and approval cards. Draft and voice stay untouched.
            activeRunID = run.id; activeSession = session; runPhase = run.status
            activities = []; streamingText = ""
            watchRun(run.id, session: session)
        } else if newRun {
            runPhase = run.status
            if let error = run.errorCode, !error.isEmpty { errorMessage = RunPresentation.errorMessage(error) }
            else if run.outcome == "unknown" { errorMessage = "El resultado de una ejecución anterior quedó sin confirmar. No se repetirá automáticamente." }
        }
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
        voice.cancel(); dictationNotice = nil
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
            observedConversationRunID = runID
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
        observedConversationRunID = runID
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
        if receipts != parsed { receipts = parsed }
    }

    func reconnect() async {
        guard runtime.isActive, !stopping, !starting, !handoffPreparing, !maintenance.blocksInteraction, !restored.blocksInteraction, let options else { return }
        conversationRefresh?.setScope(nil)
        voice.cancel(); dictationNotice = nil
        starting = true
        defer { starting = false; startMonitor() }
        polling?.cancel()
        await client?.close()
        await access.close()
        await mcp.close()
        await management.close()
        await telegram.close()
        await maintenance.close()
        await restored.close()
        restored.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        maintenance.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        guard !stopping else { return }
        do {
            let restarted = !service.isRunning
            if restarted {
                let predecessor = service.ownedIdentity
                guard await service.stop() else { throw ServiceProcessFailure.cleanupUnconfirmed }
                if let predecessor {
                    guard let report = service.lastStopReport, predecessor.matches(report), report.graceful else { throw ServiceProcessFailure.cleanupUnconfirmed }
                } else if let report = service.lastStopReport, !report.graceful { throw ServiceProcessFailure.cleanupUnconfirmed }
                guard runtime.isActive, !stopping else { return }
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
            finishStartupRecovery()
            if restarted, activeSession?.temporary == true {
                lostTemporaryRun = true
                runPhase = "disconnected"
                errorMessage = "El chat temporal se perdió al reiniciar el servicio. No se puede confirmar el resultado de su ejecución y no se repetirá."
            } else if activeRunID != nil {
                access.setContext((activeSession ?? selected)?.scope)
                management.setContext((activeSession ?? selected)?.scope)
                conversationRefresh?.setScope((activeSession ?? selected)?.scope)
                runPhase = "disconnected"
                resumeStatus()
            }
            else if let id = selectedID, sessions.contains(where: { $0.id == id }) { await selectSession(id) }
            else if let first = sessions.first { await selectSession(first.id) }
            else { await createSession(temporary: false) }
        } catch { _ = recordStartupFailure(error) }
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
        voice.cancel(); dictationNotice = nil
        guard canChangeSession,
              OwnerManagementReview.confirm(title: "Ramificar conversación", action: "Crear rama",
                detail: "Se copiará únicamente el contexto completado de «\(session.title)» a una conversación nueva del mismo agente. No se ejecutan ni copian acciones, runs o autorizaciones.", isCurrent: { runtime.isActive && !stopping && !handoffPreparing }) else { return }
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
        if selectedID == session.id { voice.cancel(); dictationNotice = nil }
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
        if selectedID == session.id { conversationRefresh?.setScope(nil) }
        if selectedID == session.id { voice.cancel(); dictationNotice = nil }
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
        conversationRefresh?.setScope(selected?.scope)
        if selectedID == nil, let first = sessions.first { await selectSession(first.id) }
    }

    func login() async {
        guard !handoffPreparing, runtime.isActive else { return }
        do {
            let result = try await rpc("provider.login")
            guard let raw = result["url"].string, let url = URL(string: raw), url.scheme == "https",
                  url.host == "auth.openai.com", url.user == nil, url.password == nil,
                  url.port == nil || url.port == 443 else {
                throw RPCFailure.invalidResponse
            }
            try runtime.check()
            guard !stopping, !handoffPreparing else { throw CancellationError() }
            guard NSWorkspace.shared.open(url) else { throw RPCFailure.unavailable }
        } catch { report(error) }
    }

    func logout() async {
        guard activeRunID == nil else { return }
        voice.cancel(); dictationNotice = nil
        do { _ = try await rpc("provider.logout"); try await refreshStatus() }
        catch { report(error) }
    }

    func stopForHandoff() async -> ServiceStopReport? {
        _ = await shutdown()
        return service.lastStopReport
    }

    func shutdown() async -> Bool {
        runtime.retire()
        conversationRefresh?.setScope(nil)
        voice.cancel(); dictationNotice = nil
        stopping = true
        ready = false
        statusText = "Cerrando el servicio y asentando las ejecuciones…"
        monitor?.cancel(); polling?.cancel()
        access.setContext(nil)
        management.setContext(nil)
        // Stopping the owned service wakes blocked RPC readers. Waiting for a
        // socket queue before sending SIGTERM could otherwise delay shutdown.
        let confirmed = await service.stop()
        guard confirmed else {
            statusText = "Cierre del servicio sin confirmar"
            errorMessage = ServiceProcessFailure.cleanupUnconfirmed.localizedDescription
            return false
        }
        await client?.close()
        await access.close()
        await mcp.close()
        await management.close()
        await telegram.close()
        await maintenance.close()
        await restored.close()
        return true
    }

    private func report(_ error: Error) {
        startupErrorMessage = nil
        errorMessage = CapabilityPresentation.errorMessage(error)
    }

    private func reportPendingStartup() {
        awaitingStartupReadiness = true
        statusText = "Arranque no confirmado"
        if errorMessage == nil || (startupErrorMessage != nil && errorMessage == startupErrorMessage) {
            let message = RPCFailure.startupUnconfirmed.localizedDescription
            errorMessage = message; startupErrorMessage = message
        }
    }

    @discardableResult
    private func recordStartupFailure(_ error: Error) -> Bool {
        if error as? RPCFailure == .startupUnconfirmed { reportPendingStartup(); return true }
        awaitingStartupReadiness = false
        report(error)
        return false
    }

    private func finishStartupRecovery() {
        guard awaitingStartupReadiness else { return }
        awaitingStartupReadiness = false
        if startupErrorMessage != nil, errorMessage == startupErrorMessage { errorMessage = nil }
        startupErrorMessage = nil
    }
}
