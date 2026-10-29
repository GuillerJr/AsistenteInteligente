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
    let plugins = PluginStore()
    let management = ManagementStore()
    let sessionCore = SessionCoreStore()
    let clientIdentities = ClientIdentityStore()
    let delegation = DelegationStore()
    let automation = NativeAutomationHost()
    let telegram = TelegramStore()
    let maintenance = MaintenanceStore()
    let restored = RestoreProfileStore()
    let channelRestoration = ChannelReconciliationStore()
    let memoryRestoration: RestoredMemoryStore
    let memoryIntelligence: MemoryIntelligenceStore
    let recovery: RecoveryExtensionStore
    let runtime: ProfileRuntimeGuard
    var localProfileStatus: LocalProfileStatus?
    var handoffPreparing = false
    @ObservationIgnored private let suppliedOptions: LaunchOptions?
    @ObservationIgnored private let openTarget: ProfileOpenTarget?
    let voice: LocalVoiceController
    let attachments: MediaAttachmentsStore
    @ObservationIgnored private let fileTranscriber = MacLocalFileTranscriber()
    @ObservationIgnored private var talkLifecycle: MacLocalVoiceConversationLifecycle?
    @ObservationIgnored private var talkRunID: String?
    @ObservationIgnored private var talkSession: ChatSession?
    @ObservationIgnored lazy var talk = LocalVoiceConversationController(voice: voice,
        currentContext: { [weak self] in self?.voiceConversationContext },
        submit: { [weak self] text, context in
            guard let self else { throw RPCFailure.disconnected }
            return try await self.submitSpokenTurn(text, context: context)
        }, cancelRun: { [weak self] context in await self?.settleSpokenRun(context) ?? false })
    var dictationNotice: String?
    var newAgentID = "jarvis"
    var lostTemporaryRun = false
    var runInputNotice: String?

    @ObservationIgnored private var client: LocalRPCClient?
    @ObservationIgnored private let service = ServiceProcess()
    @ObservationIgnored private var monitor: Task<Void, Never>?
    @ObservationIgnored private var monitorReadActive = false
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var memoryRecallRefresh: Task<Void, Never>?
    @ObservationIgnored private var memoryRecallRequestedContext: String?
    @ObservationIgnored private var started = false
    @ObservationIgnored private var options: LaunchOptions?
    @ObservationIgnored private var pendingSubmission: PendingSubmission?
    @ObservationIgnored private var pendingQueue: PendingSubmission?
    @ObservationIgnored private var activeSession: ChatSession?
    @ObservationIgnored private var stopping = false
    private var nativeCleanupPending = false
    @ObservationIgnored private var awaitingStartupReadiness = false
    @ObservationIgnored private var startupErrorMessage: String?
    @ObservationIgnored private var conversationRefresh: ConversationRefresh?
    @ObservationIgnored private var observedConversationRunID: String?
    @ObservationIgnored private var recoveringCrash = false
    @ObservationIgnored private var crashRecoveryBlocked = false
    @ObservationIgnored private var coldRecovery = false
    @ObservationIgnored private var nextCrashAttempt = ContinuousClock.now

    var isShuttingDown: Bool { stopping }
    private var lifecycleBlocksInteraction: Bool {
        starting || stopping || recoveringCrash || nativeCleanupPending || automation.lifecycleBusy || attachments.busy
    }
    var coreBlocksHost: Bool { sessionCore.blocksInteraction || clientIdentities.blocksInteraction || delegation.blocksInteraction || hasUnconfirmedQueue }
    var hasUnconfirmedQueue: Bool { pendingQueue != nil }
    var canSupplyRunInput: Bool { !lifecycleBlocksInteraction && ready && connected && !busy && !handoffPreparing && !coldRecovery
        && activeRunID != nil && selected?.scope == activeSession?.scope && !coreBlocksHost && !lostTemporaryRun
        && !maintenance.blocksInteraction && !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction
        && !recovery.blocksInteraction && !restored.blocksInteraction }
    var launchOptions: LaunchOptions? { options }
    var isRecoveryOnly: Bool { (options ?? suppliedOptions)?.recoveryOnly == true }
    var serviceOwner: ServiceOwnerIdentity? { service.ownedIdentity }
    var durableDraftSnapshot: SessionDrafts { drafts.durableOnly }
    var mayHandoff: Bool { !talk.isActive && !lifecycleBlocksInteraction && !isRecoveryOnly && ready && !handoffPreparing && !busy && activeRunID == nil && !voice.canStopDictation && !access.isChangingWorkspace && !mcp.blocksContextChanges && !plugins.isChanging && !management.isChanging && !sessionCore.blocksInteraction && !clientIdentities.blocksInteraction && !delegation.blocksInteraction && !hasUnconfirmedQueue && !telegram.isChanging && !maintenance.blocksInteraction && !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction && !recovery.blocksInteraction && !restored.isRequesting && !restored.responseUnconfirmed && restored.operation?.workerPending != true && restored.operation?.cleanupOwnerPending != true }
    var shutdownWarning: String? { service.lastStopReport?.warning }
    var latestConversationRunID: String? { observedConversationRunID }
    var mcpSelectionKey: String {
        ManagementValidation.digest([runtime.id.uuidString, options?.state ?? "", localProfileStatus?.identity.device ?? "",
            localProfileStatus?.identity.inode ?? "", selected?.id ?? "", String(selected?.temporary ?? false),
            selected?.agentID ?? "", String(management.agents.first(where: { $0.id == selected?.agentID })?.revision ?? 0),
            access.workspacePath ?? ""].joined(separator: "\0"))
    }

    var selected: ChatSession? { sessions.first { $0.id == selectedID } }
    var canSend: Bool { !talk.isActive && submissionEligible }
    private var submissionEligible: Bool { !lifecycleBlocksInteraction && !isRecoveryOnly && ready && connected && !busy && !access.isChangingWorkspace && !mcp.blocksContextChanges && !plugins.isChanging && !management.isChanging && !sessionCore.blocksInteraction && !clientIdentities.blocksInteraction && !delegation.blocksInteraction && !hasUnconfirmedQueue && !telegram.isChanging && !maintenance.blocksInteraction && !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction && !recovery.blocksInteraction && !restored.blocksInteraction && !handoffPreparing && !voice.canStopDictation && activeRunID == nil && selected != nil
                       && selected?.archived == false
                       && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.selectedIDs.isEmpty) }
    private var sessionChangeEligible: Bool { !isRecoveryOnly && ready && !busy && !access.isChangingWorkspace && !mcp.blocksContextChanges && !plugins.isChanging && !management.isChanging && !sessionCore.blocksInteraction && !clientIdentities.blocksInteraction && !delegation.blocksInteraction && !hasUnconfirmedQueue && !telegram.isChanging && !maintenance.blocksInteraction && !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction && !recovery.blocksInteraction && !restored.blocksInteraction && !handoffPreparing && !voice.canStopDictation && activeRunID == nil }
    var canChangeSession: Bool { !talk.isActive && !lifecycleBlocksInteraction && sessionChangeEligible }
    private var canInitializeSession: Bool {
        starting && !stopping && !recoveringCrash && !nativeCleanupPending && !automation.lifecycleBusy && sessionChangeEligible
    }
    var selectedAgentName: String { management.agents.first(where: { $0.id == selected?.agentID })?.name ?? selected?.agentID ?? "Jarvis" }
    var memoryRecallNotice: String? {
        guard let current = memoryIntelligence.status, current.scope == selected?.scope,
              current.policy.settings.autoRecallEnabled else { return nil }
        return current.lastRecall?.notice(for: observedConversationRunID)
    }
    var canUseVoice: Bool { canChangeSession && connected && selected?.archived == false && selected != nil }

    init(voice: LocalVoiceController? = nil, options: LaunchOptions? = nil, target: ProfileOpenTarget? = nil, runtime: ProfileRuntimeGuard = ProfileRuntimeGuard()) {
        self.runtime = runtime; memoryRestoration = RestoredMemoryStore(runtime: runtime); memoryIntelligence = MemoryIntelligenceStore(runtime: runtime); recovery = RecoveryExtensionStore(runtime: runtime); suppliedOptions = options; openTarget = target
        self.voice = voice ?? LocalVoiceController(driver: MacLocalVoiceDriver())
        attachments = MediaAttachmentsStore(isActive: { runtime.isActive }, confirmReview: { title, action, detail, current in
            OwnerManagementReview.confirm(title: title, action: action, detail: detail, isCurrent: current)
        })
        self.voice.onTranscript = { [weak self] transcript in guard let self else { return }
            if !self.talk.acceptTranscript(transcript) { self.acceptDictation(transcript) } }
        self.voice.onCaptureCompletion = { [weak self] in self?.talk.captureCompleted($0) }
        self.voice.onPlaybackCompletion = { [weak self] in self?.talk.playbackCompleted($0) }
        mcp.onRunStarted = { [weak self] run, scope in self?.observeMCPRun(run, scope: scope) }
        plugins.onRunStarted = { [weak self] run, scope in self?.observeMCPRun(run, scope: scope) }
        maintenance.waitForSupervisionIdle = { [weak self] in
            guard let self else { throw RPCFailure.configuration }
            try await self.waitForMonitorSettlement()
        }
        channelRestoration.waitForSupervisionIdle = { [weak self] in
            guard let self else { throw RPCFailure.configuration }; try await self.waitForMonitorSettlement()
        }
        memoryRestoration.waitForSupervisionIdle = { [weak self] in
            guard let self else { throw RPCFailure.configuration }
            try await self.waitForMonitorSettlement()
        }
        memoryIntelligence.waitForSupervisionIdle = { [weak self] in
            guard let self else { throw RPCFailure.configuration }
            try await self.waitForMonitorSettlement()
        }
        memoryIntelligence.isContextCurrent = { [weak self] scope, key in
            guard let self else { return false }
            return runtime.isActive && !stopping && !starting && !recoveringCrash && !handoffPreparing
                && !access.isChangingWorkspace && selected?.scope == scope && memoryIntelligenceContextKey == key
        }
        recovery.waitForSupervisionIdle = { [weak self] in
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
        talk.stop(); dictationNotice = nil
        voice.startSelectedDictation(contextID: LocalVoiceDraftBridge.contextID(selected.scope, runtimeID: runtime.id))
    }

    func readMessage(_ message: ChatMessage) {
        guard canUseVoice, message.role == "assistant", messages.contains(where: { $0.id == message.id }), let selected else { return }
        talk.stop()
        voice.playSelected(text: message.text, contextID: LocalVoiceDraftBridge.contextID(selected.scope, runtimeID: runtime.id))
    }

    func stopVoice() { talk.stop(); talkLifecycle?.close(); talkLifecycle = nil; voice.cancel() }

    private func acceptDictation(_ transcript: LocalVoiceTranscript) {
        let mayAcceptDraft = !lifecycleBlocksInteraction && ready && connected && !handoffPreparing && selected?.archived == false && selected != nil
        guard let value = LocalVoiceDraftBridge.append(transcript, scope: selected?.scope, existing: draft, eligible: mayAcceptDraft, runtimeID: runtime.id) else {
            dictationNotice = "El dictado no se añadió: la conversación cambió o se alcanzó el límite del borrador."
            return
        }
        draft = value
        dictationNotice = transcript.completion == .partialAtStop
            ? "Se añadió un dictado parcial al borrador. Revísalo antes de enviarlo."
            : "Dictado local añadido al borrador. Revísalo antes de enviarlo."
    }

    private var voiceConversationContext: LocalVoiceConversationContext? {
        guard runtime.isActive, ready, connected, !stopping, !recoveringCrash,
              !handoffPreparing, !access.isChangingWorkspace, let session = selected,
              !session.archived else { return nil }
        return LocalVoiceConversationContext(
            contextID: LocalVoiceDraftBridge.contextID(session.scope, runtimeID: runtime.id),
            revision: mcpSelectionKey)
    }

    func startVoiceConversation() {
        guard canUseVoice, draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              attachments.selectedIDs.isEmpty, let context = voiceConversationContext else { return }
        guard OwnerManagementReview.confirm(title: "Conversar por voz", action: "Iniciar hasta tres turnos",
            detail: "El micrófono se abrirá de forma visible, hasta tres turnos o cinco minutos. El reconocimiento y la lectura usan el Mac. Sólo el TEXTO final reconocido se enviará a Codex con ChatGPT. El micrófono se cierra mientras Codex responde y durante la lectura. El modo se detiene ante una aprobación, cambio de conversación o pérdida de foco. Las herramientas conservan sus permisos habituales.",
            isCurrent: { self.voiceConversationContext == context && self.canUseVoice
                && self.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && self.attachments.selectedIDs.isEmpty }) else { return }
        talkRunID = nil; talkSession = nil
        if talk.start(context: context) {
            talkLifecycle?.close()
            talkLifecycle = MacLocalVoiceConversationLifecycle(talk: talk, voice: voice)
        }
    }

    func preserveSpokenPartial() {
        guard let context = voiceConversationContext,
              let transcript = talk.takePartialForReview(context: context) else { return }
        acceptDictation(transcript)
    }

    private func submitSpokenTurn(_ text: String, context: LocalVoiceConversationContext) async throws -> LocalVoiceConversationReply {
        guard voiceConversationContext == context, draft.isEmpty, attachments.selectedIDs.isEmpty else { throw RPCFailure.disconnected }
        draft = text
        await performSend(spokenContext: context)
        guard let id = talkRunID, let session = talkSession else { throw RPCFailure.invalidResponse }
        var params = scope(session); params["run_id"] = .string(id)
        let deadline = ContinuousClock.now.advanced(by: .seconds(180))
        while ContinuousClock.now < deadline {
            try Task.checkCancellation()
            guard voiceConversationContext == context else { throw RPCFailure.disconnected }
            let result = try await rpc("runs.get", params)["run"]
            guard result["run_id"] == .string(id), let status = result["status"].string else { throw RPCFailure.invalidResponse }
            if status == "waiting_approval" {
                talk.approvalBecamePending(); throw CancellationError()
            }
            if ["completed", "failed", "cancelled", "interrupted"].contains(status) {
                guard let outcome = result["outcome"].string, let receipts = result["action_receipts"].array else { throw RPCFailure.invalidResponse }
                let parsed = try receipts.map(ActionReceipt.init)
                return LocalVoiceConversationReply(runID: id, status: status, outcome: outcome,
                    answer: result["answer"].string ?? "", unconfirmedEffects: parsed.contains(where: \.unknown))
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw RPCFailure.unavailable
    }

    private func settleSpokenRun(_ context: LocalVoiceConversationContext) async -> Bool {
        guard let id = talkRunID, let session = talkSession else { return true }
        guard LocalVoiceDraftBridge.contextID(session.scope, runtimeID: runtime.id) == context.contextID,
              let client, runtime.isActive else { return false }
        var params = scope(session); params["run_id"] = .string(id)
        do {
            // The cancellation ACK is not settlement. Poll the same durable run;
            // never cancel a later manual or Telegram run in this conversation.
            let before = try await client.call("runs.get", params: params)["run"]
            if !["completed", "failed", "cancelled", "interrupted"].contains(before["status"].string ?? "") {
                _ = try await client.call("runs.cancel", params: params)
            }
            let deadline = ContinuousClock.now.advanced(by: .seconds(20))
            while ContinuousClock.now < deadline {
                let value = try await client.call("runs.get", params: params)["run"]
                guard value["run_id"] == .string(id), let phase = value["status"].string,
                      let receipts = value["action_receipts"].array else { return false }
                _ = try receipts.map(ActionReceipt.init)
                if ["completed", "failed", "cancelled", "interrupted"].contains(phase) {
                    if talkRunID == id { talkRunID = nil; talkSession = nil }
                    return true
                }
                try await Task.sleep(for: .milliseconds(250))
            }
        } catch { return false }
        return false
    }

    func start() async {
        guard !started, runtime.isActive else { return }
        started = true
        starting = true
        defer { starting = false }
        var startupRead = "la identidad y el estado local"
        do {
            let options = try suppliedOptions ?? LaunchOptions.parse(Array(CommandLine.arguments.dropFirst()))
            self.options = options
            configureConnections(options)
            try service.start(options)
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try service.confirmReadyProfile(options)
            if !isRecoveryOnly { await automation.start() }
            if isRecoveryOnly {
                try await refreshRecoveryStatus()
                startMonitor()
                return
            }
            try await refreshStatus()
            startupRead = "los agentes y las tareas conservadas"
            try await management.refresh()
            startupRead = "las conversaciones conservadas"
            try await refreshSessions()
            if sessions.isEmpty, options.expectedRestore == nil { await createSession(temporary: false, duringStartup: true) }
            else if let first = sessions.first { await selectSession(first.id, duringStartup: true) }
            startupRead = "el catálogo de herramientas"
            try await access.refreshCatalog()
            synchronizeMemoryIntelligenceContext()
            startupRead = "las conexiones MCP"
            try await mcp.refresh()
            startMonitor()
        } catch {
            if openTarget != nil, error as? RPCFailure == .invalidResponse {
                _ = await shutdown()
                statusText = "No se pudo validar \(startupRead) del perfil restaurado"
                report(error)
                return
            }
            if recordStartupFailure(error) { startMonitor() }
            else { statusText = "Jarvis necesita configuración" }
        }
    }

    private func configureConnections(_ options: LaunchOptions) {
        let connection = LocalRPCClient(stateDirectory: options.state, runtime: runtime)
        client = connection
        automation.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime),
            testOrigins: Set(options.automationTestOrigin.map { [$0] } ?? []))
        conversationRefresh = ConversationRefresh(reader: ConversationReader(transport: connection))
        attachments.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        automation.mediaTranscriber = { [weak self] data, name, mime, sha, locale in
            guard let self else { throw RPCFailure.disconnected }
            return try await self.fileTranscriber.transcribe(data: data, name: name, mimeType: mime, localeIdentifier: locale, expectedSHA256: sha)
        }
        access.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        mcp.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        plugins.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        management.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        sessionCore.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        clientIdentities.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        delegation.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        telegram.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        maintenance.configure(client: LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        restored.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        channelRestoration.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        memoryRestoration.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        memoryIntelligence.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
        recovery.configure(LocalRPCClient(stateDirectory: options.state, runtime: runtime))
    }

    /// Hashes local context metadata only. Physical policy/workspace authority is revalidated by the service.
    private var memoryIntelligenceContextKey: String {
        let components = [runtime.id.uuidString, options?.state ?? "", localProfileStatus?.identity.device ?? "",
            localProfileStatus?.identity.inode ?? "", selected?.agentID ?? "", access.workspacePath ?? "",
            access.workspacePrivateDefault ? "private" : "selected", access.workspaceAvailable ? "available" : "unavailable",
            access.workspaceError ?? ""]
        return ManagementValidation.digest(components.map { "\($0.utf8.count):\($0)" }.joined())
    }
    var memoryIntelligenceSelectionKey: String { memoryIntelligenceContextKey + ":" + (selected?.id ?? "none") }
    func synchronizeMemoryIntelligenceContext() {
        memoryIntelligence.select(scope: isRecoveryOnly ? nil : selected?.scope, contextKey: memoryIntelligenceContextKey)
    }
    /// Only current-chat metadata. Run/provider polling never waits for this read.
    private func requestMemoryRecallStatus(for session: ChatSession) {
        guard !isRecoveryOnly, !stopping, !handoffPreparing, selected?.scope == session.scope else { return }
        synchronizeMemoryIntelligenceContext()
        let key = memoryIntelligenceContextKey
        let requestKey = key + ":" + session.id + ":" + (observedConversationRunID ?? "none")
        if memoryRecallRequestedContext == requestKey, memoryIntelligence.status?.scope == session.scope { return }
        memoryRecallRefresh?.cancel()
        memoryRecallRefresh = Task { [weak self] in
            guard let self else { return }
            let deadline = ContinuousClock.now.advanced(by: .seconds(10))
            while starting || recoveringCrash || memoryIntelligence.isBusy {
                guard !Task.isCancelled, !stopping, runtime.isActive, selected?.scope == session.scope,
                      memoryIntelligenceContextKey == key, ContinuousClock.now < deadline else { return }
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
            }
            guard !Task.isCancelled, !stopping, runtime.isActive, selected?.scope == session.scope,
                  memoryIntelligenceContextKey == key else { return }
            memoryRecallRequestedContext = requestKey
            await memoryIntelligence.refresh()
            if memoryIntelligence.status?.scope != session.scope { memoryRecallRequestedContext = nil }
        }
    }

    private func startMonitor() {
        guard !stopping else { return }
        monitor?.cancel()
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
                guard let self else { return }
                let supervision = ServiceSupervisionPolicy.decision(childRunning: self.service.isRunning,
                    lifecycleChanging: self.lifecycleBlocksInteraction || self.handoffPreparing,
                    recoveryBlocked: self.crashRecoveryBlocked,
                    readersPaused: self.maintenance.monitorPaused || self.channelRestoration.monitorPaused
                        || self.memoryRestoration.monitorPaused || self.memoryIntelligence.monitorPaused || self.recovery.monitorPaused || self.restored.monitorPaused)
                if supervision == .recover { _ = await self.recoverCrashedService(); continue }
                if supervision != .read { continue }
                do {
                    try await self.refreshMonitoredState()
                } catch {
                    if Task.isCancelled || self.stopping { return }
                    if MaintenanceMonitorPolicy.isTemporaryBusy(error) { continue }
                    self.ready = false; self.connected = false
                    self.stopVoice()
                    if !self.service.isRunning, !self.crashRecoveryBlocked {
                        if await self.recoverCrashedService() { continue }
                        if self.recoveringCrash || ContinuousClock.now < self.nextCrashAttempt { continue }
                    }
                    if !self.service.isRunning, self.crashRecoveryBlocked { continue }
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
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        monitorReadActive = true
        defer { monitorReadActive = false }
        guard service.isRunning else { throw RPCFailure.disconnected }
        if awaitingStartupReadiness, let options = options {
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state, timeout: 1) { service.isRunning }
            try service.confirmReadyProfile(options)
            if !isRecoveryOnly { await automation.start() }
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        }
        if isRecoveryOnly {
            try await refreshRecoveryStatus()
            finishStartupRecovery()
            return
        }
        if coldRecovery {
            try await refreshColdRecoveryState()
            return
        }
        try await refreshStatus()
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await access.refreshCatalog()
        synchronizeMemoryIntelligenceContext()
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await mcp.refresh()
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await management.refresh()
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        if canChangeSession { try await refreshSessions() }
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        try await refreshSelectedConversation()
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
        if activeRunID == nil { try await access.refreshApprovals(runID: nil) }
        guard !maintenance.monitorPaused, !channelRestoration.monitorPaused, !memoryRestoration.monitorPaused, !memoryIntelligence.monitorPaused, !recovery.monitorPaused, !restored.monitorPaused, !handoffPreparing else { return }
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

    /// Restart only the exact owned child whose exit is kernel-confirmed and
    /// whose inherited profile lease is free. This never retries an RPC effect.
    private func recoverCrashedService() async -> Bool {
        guard !recoveringCrash, !crashRecoveryBlocked, !starting, !stopping, !handoffPreparing,
              runtime.isActive, let options, !service.isRunning, ContinuousClock.now >= nextCrashAttempt else { return false }
        recoveringCrash = true
        defer { recoveringCrash = false }
        ready = false; connected = false
        stopVoice(); dictationNotice = nil
        nextCrashAttempt = ContinuousClock.now.advanced(by: .seconds(3))
        do {
            access.setContext(nil); management.setContext(nil)
            let previous = try service.prepareCrashRecovery(options)
            nativeCleanupPending = true
            guard await automation.stop(handshake: false) else { throw ServiceProcessFailure.cleanupUnconfirmed }
            nativeCleanupPending = false
            polling?.cancel(); memoryRecallRefresh?.cancel(); conversationRefresh?.setScope(nil)
            await client?.close(); await access.close(); await mcp.close(); await plugins.close(); await management.close()
            await sessionCore.close(); await clientIdentities.close(); await delegation.close(); await telegram.close()
            await maintenance.close(); await restored.close(); await channelRestoration.close()
            await memoryRestoration.close(); await memoryIntelligence.close(); await recovery.close()
            guard !stopping, runtime.isActive else { return false }
            configureConnections(options)
            try service.start(options)
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try service.confirmReadyProfile(options)
            if !isRecoveryOnly { await automation.start() }
            coldRecovery = true; ready = true; connected = false
            if pendingQueue != nil { runInputNotice = "El envío previo quedó en el registro local. No se reenviará tras recuperar el servicio." }
            pendingQueue = nil
            drafts.clearTemporary(); sessions.removeAll(where: \.temporary)
            if activeSession?.temporary == true {
                lostTemporaryRun = true; runPhase = "disconnected"
            } else {
                activeRunID = nil; activeSession = nil; pendingSubmission = nil; runPhase = "interrupted"
            }
            access.setContext(selected?.scope); management.setContext(selected?.scope)
            sessionCore.setScope(selected?.scope); delegation.setScope(selected?.scope)
            synchronizeMemoryIntelligenceContext()
            conversationRefresh?.setScope(selected?.scope)
            if isRecoveryOnly { try await refreshRecoveryStatus() }
            else { try await refreshColdRecoveryState() }
            errorMessage = previous.warning ?? "El servicio se recuperó. Los resultados anteriores se conservan sin repetir operaciones."
            return true
        } catch {
            ready = false; connected = false
            if let failure = error as? ServiceProcessFailure, failure == .recoveryResourcesBusy {
                statusText = failure.localizedDescription
            } else {
                crashRecoveryBlocked = true; statusText = "Recuperación automática detenida"; report(error)
            }
            return false
        }
    }

    private func refreshColdRecoveryState() async throws {
        localProfileStatus = try await readLocalStatus()
        try await management.refresh(); try await access.refreshCatalog(); synchronizeMemoryIntelligenceContext(); try await mcp.refresh()
        try await refreshSessions()
        try await refreshSelectedConversation()
        ready = true; connected = false
        statusText = "Servicio recuperado · cuentas desconectadas · revisa los resultados anteriores"
    }

    func readLocalStatus() async throws -> LocalProfileStatus {
        let value = try await LocalProfileStatus(rpc("service.local_status"))
        if let target = openTarget, !value.matches(target) { throw RPCFailure.invalidResponse }
        return value
    }

    private func refreshRecoveryStatus() async throws {
        guard let client else { throw RPCFailure.configuration }
        localProfileStatus = try await RecoveryBootstrap.inspect { try await client.call($0, params: $1) }
        try runtime.check()
        guard !stopping else { throw CancellationError() }
        ready = true; connected = false
        statusText = "Recuperación local · cuentas y conversaciones sin cargar"
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
        if !serverReady || !accountConnected { stopVoice() }
        talk.validateContext()
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
        synchronizeMemoryIntelligenceContext()
    }

    func createSession(temporary: Bool) async {
        await createSession(temporary: temporary, duringStartup: false)
    }

    private func createSession(temporary: Bool, duringStartup: Bool) async {
        guard duringStartup ? canInitializeSession : canChangeSession else { return }
        stopVoice(); dictationNotice = nil
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
            await mcp.setContext(session.scope)
            guard mcp.context == session.scope, !mcp.appCleanupUnconfirmed else { throw RPCFailure.unavailable }
            sessions.insert(session, at: 0)
            selectedID = session.id
            access.setContext(session.scope)
            management.setContext(session.scope)
            sessionCore.setScope(session.scope)
            delegation.setScope(session.scope)
            synchronizeMemoryIntelligenceContext()
            messages = []; activities = []; draft = ""; streamingText = ""; errorMessage = nil
            receipts = []
            observedConversationRunID = nil
        } catch { report(error) }
    }

    func selectSession(_ id: String) async {
        await selectSession(id, duringStartup: false)
    }

    private func selectSession(_ id: String, duringStartup: Bool) async {
        guard duringStartup ? canInitializeSession : canChangeSession,
              let session = sessions.first(where: { $0.id == id }) else { return }
        conversationRefresh?.setScope(nil)
        stopVoice(); dictationNotice = nil
        busy = true
        defer { busy = false; conversationRefresh?.setScope(selected?.scope) }
        do {
            let rows = try await fetchMessages(session)
            await mcp.setContext(session.scope)
            guard mcp.context == session.scope, !mcp.appCleanupUnconfirmed else { throw RPCFailure.unavailable }
            selectedID = id
            await attachments.setScope(session.scope)
            access.setContext(session.scope)
            management.setContext(session.scope)
            sessionCore.setScope(session.scope)
            delegation.setScope(session.scope)
            synchronizeMemoryIntelligenceContext()
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

    private func observeMCPRun(_ id: String, scope: SessionScope) {
        guard runtime.isActive, !stopping, let session = selected, session.scope == scope,
              mcp.context == scope, ManagementValidation.identifier(id), activeRunID == nil || activeRunID == id else { return }
        activeRunID = id; activeSession = session; observedConversationRunID = id
        runPhase = "queued"; activities = []; streamingText = ""
        watchRun(id, session: session)
    }

    private func decodeMessages(_ rows: [RPCValue], scope: SessionScope) throws -> [ChatMessage] {
        try rows.map { row in
            let message = try ConversationMessage(row, scope: scope)
            return chatMessage(message, scope: scope)
        }
    }

    private func chatMessage(_ message: ConversationMessage, scope: SessionScope) -> ChatMessage {
        ChatMessage(id: "\(scope.temporary):\(scope.sessionID):\(message.id)", role: message.role,
                    text: SessionContextPresentation.systemRecord(message.text, role: message.role) ?? message.text,
                    sequence: message.sequence)
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
        if !run.active { requestMemoryRecallStatus(for: session) }
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
        guard canSend else { return }
        await performSend()
    }

    private func performSend(spokenContext: LocalVoiceConversationContext? = nil) async {
        guard submissionEligible, let session = selected else { return }
        voice.cancel(); dictationNotice = nil
        busy = true
        defer { busy = false }
        var params = scope(session)
        let submittedText = draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Revisa estos adjuntos." : draft
        params["message"] = .string(submittedText)
        let attachmentIDs = attachments.selectedIDs
        if !attachmentIDs.isEmpty { params["attachment_ids"] = .array(attachmentIDs.map(RPCValue.string)) }
        let requestID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        params["request_id"] = .string(requestID)
        let submission = PendingSubmission(id: requestID, session: session, text: submittedText, attachmentIDs: attachmentIDs)
        pendingSubmission = submission
        if spokenContext != nil { talkRunID = requestID; talkSession = session }
        activeSession = session
        errorMessage = nil
        streamingText = ""
        do {
            let result = try await rpc("runs.start", params)
            guard let runID = result["run_id"].string else { throw RPCFailure.invalidResponse }
            activeRunID = runID
            if spokenContext != nil { talkRunID = runID }
            attachments.didSubmit(attachmentIDs, scope: session.scope)
            observedConversationRunID = runID
            runPhase = result["status"].string ?? "queued"
            if draft == submission.text || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { draft = "" }
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
                                if kind == "coordination.created", let goalID = row["payload"]["goal_id"].string {
                                    await self.delegation.observe(scope: session.scope, parentRunID: runID, goalID: goalID)
                                }
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
                    if phase == "waiting_approval" || !self.access.approvals.isEmpty { self.talk.approvalBecamePending() }
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
                        try await self.recoverLatestRun(session)
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
        var firstActive: RPCValue?
        var complete = false
        for _ in 0..<20 {
            var parameters = scope(session)
            parameters["offset"] = .number(Double(offset))
            let result = try await rpc("runs.list", parameters)
            guard let rows = result["runs"].array, let more = result["has_more"].bool,
                  let next = result["next_offset"].integer else { throw RPCFailure.invalidResponse }
            latest = rows.last ?? latest
            if firstActive == nil {
                firstActive = rows.first(where: { ["queued", "running", "waiting_approval", "cancelling", "cleaning"].contains($0["status"].string ?? "") })
            }
            if !more { complete = true; break }
            guard next > offset else { throw RPCFailure.invalidResponse }
            offset = next
        }
        guard complete else { throw RPCFailure.oversized }
        guard let latest = firstActive ?? latest, let runID = latest["run_id"].string,
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
        if !["queued", "running", "waiting_approval", "cancelling", "cleaning"].contains(phase) {
            requestMemoryRecallStatus(for: session)
        }
    }

    private func applyReceipts(_ run: RPCValue) throws {
        guard let values = run["action_receipts"].array else { throw RPCFailure.invalidResponse }
        let parsed = try values.map(ActionReceipt.init)
        guard Set(parsed.map(\.id)).count == parsed.count else { throw RPCFailure.invalidResponse }
        if receipts != parsed { receipts = parsed }
    }

    func reconnect() async {
        if runtime.isActive, !stopping, !starting, !automation.lifecycleBusy, !handoffPreparing, !service.isRunning, service.ownedIdentity != nil {
            crashRecoveryBlocked = false; nextCrashAttempt = .now
            _ = await recoverCrashedService(); startMonitor(); return
        }
        guard runtime.isActive, !stopping, !starting, !automation.lifecycleBusy, !handoffPreparing, !maintenance.blocksInteraction, !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction && !recovery.blocksInteraction, !restored.blocksInteraction, !sessionCore.blocksInteraction, !clientIdentities.blocksInteraction, !delegation.blocksInteraction, !hasUnconfirmedQueue, let options else { return }
        starting = true
        ready = false; connected = false
        nativeCleanupPending = true
        statusText = "Reconectando y esperando la limpieza de las herramientas…"
        defer { starting = false; startMonitor() }
        conversationRefresh?.setScope(nil)
        stopVoice(); dictationNotice = nil
        guard await automation.stop(handshake: service.isRunning) else {
            statusText = "Reconexión pendiente: cierre nativo sin confirmar"
            errorMessage = "El cierre de las herramientas nativas no está confirmado. Reintenta la conexión para comprobarlo."
            return
        }
        nativeCleanupPending = false
        guard !stopping, runtime.isActive else { return }
        polling?.cancel(); memoryRecallRefresh?.cancel()
        await client?.close()
        await attachments.close()
        await access.close()
        await mcp.close()
        await plugins.close()
        await management.close()
        await sessionCore.close()
        await clientIdentities.close()
        await delegation.close()
        await telegram.close()
        await maintenance.close()
        await restored.close()
        await channelRestoration.close()
        await memoryRestoration.close()
        await memoryIntelligence.close()
        await recovery.close()
        guard !stopping, runtime.isActive else { return }
        configureConnections(options)
        sessionCore.setScope(selected?.scope)
        delegation.setScope(selected?.scope)
        synchronizeMemoryIntelligenceContext()
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
                sessionCore.setScope(nil)
                delegation.setScope(nil)
                synchronizeMemoryIntelligenceContext()
            }
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try service.confirmReadyProfile(options)
            if !isRecoveryOnly { await automation.start() }
            if isRecoveryOnly {
                try await refreshRecoveryStatus()
                finishStartupRecovery()
                startMonitor()
                return
            }
            if coldRecovery {
                try await refreshColdRecoveryState()
                finishStartupRecovery(); startMonitor(); return
            }
            try await refreshStatus()
            try await refreshSessions()
            try await access.refreshCatalog()
            synchronizeMemoryIntelligenceContext()
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
                sessionCore.setScope((activeSession ?? selected)?.scope)
                delegation.setScope((activeSession ?? selected)?.scope)
                synchronizeMemoryIntelligenceContext()
                conversationRefresh?.setScope((activeSession ?? selected)?.scope)
                runPhase = "disconnected"
                resumeStatus()
            }
            else if let id = selectedID, sessions.contains(where: { $0.id == id }) { await selectSession(id, duringStartup: true) }
            else if let first = sessions.first { await selectSession(first.id, duringStartup: true) }
            else { await createSession(temporary: false, duringStartup: true) }
        } catch { _ = recordStartupFailure(error) }
    }

    func acknowledgeLostTemporaryRun() {
        guard lostTemporaryRun else { return }
        lostTemporaryRun = false; activeRunID = nil; pendingSubmission = nil; activeSession = nil
        selectedID = nil; messages = []; streamingText = ""; activities = []; receipts = []
        access.setContext(nil)
        management.setContext(nil)
        sessionCore.setScope(nil)
        delegation.setScope(nil)
        synchronizeMemoryIntelligenceContext()
        runPhase = "interrupted"
    }

    func renameSession(_ session: ChatSession, title: String) async -> Bool {
        guard canChangeSession else { return false }
        var parameters = scope(session); parameters["title"] = .string(title)
        return await updateSession(parameters)
    }

    func forkSession(_ session: ChatSession) async {
        guard canChangeSession else { return }
        stopVoice(); dictationNotice = nil
        if selectedID != session.id { await selectSession(session.id) }
        guard selected?.scope == session.scope else { return }
        await refreshSessionContext()
        guard canChangeSession, let context = sessionCore.snapshot,
              OwnerManagementReview.confirm(title: "Ramificar conversación", action: "Crear esta rama",
                detail: "Se copiará el contexto completado de «\(session.title)», revisión \(context.revision), junto con decisiones y referencias históricas a recibos. El original se conserva. No hereda autorizaciones, ejecuciones ni el hilo del proveedor; los efectos anteriores no se repiten.",
                isCurrent: { canChangeSession && selected?.scope == session.scope && sessionCore.snapshot?.revision == context.revision }) else { return }
        await sessionCore.fork(title: session.title)
        if let branch = sessionCore.result?.branch { await openSessionBranch(branch) }
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
        if selectedID == session.id { stopVoice(); dictationNotice = nil }
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
        if selectedID == session.id { stopVoice(); dictationNotice = nil }
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
                sessionCore.setScope(nil)
                delegation.setScope(nil)
                synchronizeMemoryIntelligenceContext()
            }
        } catch { report(error) }
        busy = false
        conversationRefresh?.setScope(selected?.scope)
        if selectedID == nil, let first = sessions.first { await selectSession(first.id) }
    }

    func login() async {
        guard !isRecoveryOnly, !coreBlocksHost, !handoffPreparing, !maintenance.blocksInteraction, !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction && !recovery.blocksInteraction, runtime.isActive else { return }
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
            coldRecovery = false
        } catch { report(error) }
    }

    func logout() async {
        guard !isRecoveryOnly, activeRunID == nil, !coreBlocksHost, !maintenance.blocksInteraction, !channelRestoration.blocksInteraction && !memoryRestoration.blocksInteraction && !memoryIntelligence.blocksInteraction && !recovery.blocksInteraction else { return }
        stopVoice(); dictationNotice = nil
        do { _ = try await rpc("provider.logout"); try await refreshStatus() }
        catch { report(error) }
    }

    func stopForHandoff() async -> ServiceStopReport? {
        guard await shutdown() else { return nil }
        return service.lastStopReport
    }

    func shutdown() async -> Bool {
        stopping = true
        ready = false; connected = false
        nativeCleanupPending = true
        statusText = "Cerrando las herramientas y esperando su limpieza…"
        conversationRefresh?.setScope(nil)
        stopVoice(); dictationNotice = nil
        guard await automation.stop(handshake: service.isRunning) else {
            statusText = "Cierre nativo sin confirmar"
            errorMessage = "El cierre de las herramientas nativas no está confirmado."
            return false
        }
        nativeCleanupPending = false
        // Runtime ownership is retired only after native cleanup is confirmed.
        // A failed attempt leaves the same runtime alive for a shutdown retry.
        runtime.retire()
        statusText = "Cerrando el servicio y asentando las ejecuciones…"
        monitor?.cancel(); polling?.cancel(); memoryRecallRefresh?.cancel()
        access.setContext(nil)
        management.setContext(nil)
        sessionCore.setScope(nil)
        delegation.setScope(nil)
        synchronizeMemoryIntelligenceContext()
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
        await plugins.close()
        await management.close()
        await sessionCore.close()
        await clientIdentities.close()
        await delegation.close()
        await telegram.close()
        await maintenance.close()
        await restored.close()
        await channelRestoration.close()
        await memoryRestoration.close()
        await memoryIntelligence.close()
        await recovery.close()
        return true
    }

    func sendRunInput(_ message: String, queued: Bool) async {
        guard canSupplyRunInput, let runID = activeRunID, let session = activeSession,
              ManagementValidation.text(message, limit: queued ? 32000 : 8000) else { return }
        guard OwnerManagementReview.confirm(title: queued ? "Poner mensaje en cola" : "Corregir turno activo",
            action: queued ? "Poner este mensaje en cola" : "Enviar esta corrección",
            detail: "Conversación «\(session.title)», ejecución \(runID):\n\n\(message)\n\n" + (queued
                ? "El mensaje esperará el cierre y el recibo de la ejecución anterior. Si hay efectos sin confirmar, no continuará automáticamente."
                : "Sólo se enviará al turno activo. Una respuesta incierta no permite repetir la corrección automáticamente."),
            isCurrent: { canSupplyRunInput && activeRunID == runID && selected?.scope == session.scope }) else { return }
        let request = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        var params = scope(session); params["message"] = .string(message); params["request_id"] = .string(request)
        busy = true
        defer { busy = false }
        if queued { pendingQueue = PendingSubmission(id: request, session: session, text: message) }
        do {
            if queued {
                _ = try await RunInputReply(queued: rpc("runs.enqueue", params), requestID: request)
                pendingQueue = nil; runInputNotice = "Mensaje registrado en cola: \(request). Esperará el asentamiento anterior."
                try await refreshMessages(session)
            } else {
                params["run_id"] = .string(runID)
                let result = try await RunInputReply(steering: rpc("runs.steer", params), runID: runID, requestID: request)
                runInputNotice = result.status == "accepted" ? "Corrección \(request) aceptada por el turno."
                    : "Corrección \(request) sin confirmar. No se reenviará automáticamente."
            }
        } catch {
            if queued {
                if RunPresentation.startWasRejected(error) { pendingQueue = nil }
                runInputNotice = pendingQueue == nil ? "El envío en cola fue rechazado."
                    : "Envío \(request) sin confirmar. Consulta su registro; no se repetirá automáticamente."
            } else { runInputNotice = "Corrección \(request) sin confirmar. No se reenviará automáticamente." }
            report(error)
        }
    }

    func inspectQueuedInput() async {
        guard ready, !busy, !stopping, let pending = pendingQueue else { return }
        busy = true; defer { busy = false }
        var params = scope(pending.session); params["run_id"] = .string(pending.id)
        do {
            let result = try await rpc("runs.get", params)
            _ = try ConversationRun(result["run"], expectedID: pending.id)
            pendingQueue = nil
            runInputNotice = "Se encontró el registro de \(pending.id). Esta consulta no confirma la recepción original ni vuelve a ponerlo en cola."
        } catch { report(error) }
    }

    func refreshDelegation() async {
        guard ready, !stopping, !handoffPreparing else { return }
        delegation.setScope(selected?.scope)
        synchronizeMemoryIntelligenceContext()
        await delegation.reloadCatalogue()
        await delegation.refresh()
        if delegation.goal?.settled == false { delegation.resumePolling() }
    }

    func startDelegation(objective: String, children: [DelegatedTaskSpec], timeout: Int) async {
        guard canChangeSession, connected, let selected, delegation.scope == selected.scope else { return }
        let profilesCurrent = { children.allSatisfy { child in self.management.agents.contains(where: { $0.id == child.agentID && $0.revision == child.agentRevision }) } }
        guard profilesCurrent() else { errorMessage = "Un agente cambió o ya no está disponible. Revisa el catálogo antes de delegar."; return }
        let steps = children.reduce(0) { $0 + $1.maxSteps }
        let detail = "Objetivo: \(objective)\nPresupuesto total: \(steps) pasos, plazo \(timeout) s.\n\n" + children.enumerated().map { index, child in
            "Tarea \(index + 1): agente \(child.agentID), revisión \(child.agentRevision), máximo \(child.maxSteps) pasos.\n\(child.message)"
        }.joined(separator: "\n\n") + "\n\nCada tarea tendrá una sesión nueva y requerirá su propia autorización para herramientas. No hereda permisos ni historial."
        guard OwnerManagementReview.confirm(title: "Delegar este objetivo", action: "Iniciar estas tareas", detail: detail,
            isCurrent: { canChangeSession && connected && self.selected?.scope == selected.scope && profilesCurrent() }) else {
            if !profilesCurrent() { errorMessage = "La revisión del agente cambió durante la decisión. No se iniciaron tareas." }
            return
        }
        await delegation.start(objective: objective, children: children, maxSteps: steps, timeout: timeout)
        if let goal = delegation.goal {
            activeRunID = goal.parentRunID; activeSession = selected; observedConversationRunID = goal.parentRunID
            watchRun(goal.parentRunID, session: selected)
        }
    }

    func steerDelegatedChild(_ child: DelegatedChildSnapshot, message: String) async {
        guard ready, !stopping, !handoffPreparing, let goal = delegation.goal,
              goal.scope == selected?.scope, !goal.settled, goal.children.contains(child), !child.settled,
              OwnerManagementReview.confirm(title: "Corregir tarea delegada", action: "Enviar esta corrección",
                detail: "Tarea \(child.index + 1), agente \(child.agentID), ejecución \(child.runID):\n\n\(message)\n\nSe enviará únicamente al turno activo de esta tarea. Si no se confirma, no se repetirá automáticamente.",
                isCurrent: { ready && !stopping && !handoffPreparing && delegation.goal?.id == goal.id }) else { return }
        await delegation.steer(child: child, message: message)
    }

    func openDelegatedChild(_ child: DelegatedChildSnapshot) async {
        guard canChangeSession, let goal = delegation.goal, goal.settled,
              goal.scope == selected?.scope, goal.children.contains(child) else { return }
        do {
            if goal.scope.temporary, !sessions.contains(where: { $0.id == child.sessionID }) {
                let value: RPCValue = .object(["session_id": .string(child.sessionID), "temporary": .bool(true),
                    "title": .string("Tarea delegada \(child.index + 1)"), "archived": .bool(false),
                    "agent_id": .string(child.agentID), "parent_session_id": .string(goal.scope.sessionID)])
                guard let session = ChatSession(value) else { throw RPCFailure.invalidResponse }; sessions.insert(session, at: 0)
            } else { try await refreshSessions() }
            await selectSession(child.sessionID)
        } catch { report(error) }
    }

    func refreshSessionContext() async {
        guard ready, !busy, !stopping, !handoffPreparing else { return }
        sessionCore.setScope(selected?.scope)
        delegation.setScope(selected?.scope)
        synchronizeMemoryIntelligenceContext()
        await sessionCore.refresh()
    }

    func compactSession(decisions: String) async {
        let retained = decisions.split(whereSeparator: \.isNewline).map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { !$0.isEmpty }
        guard retained.count <= 16, retained.allSatisfy({ $0.utf8.count <= 1024 }) else {
            errorMessage = "Conserva como máximo 16 decisiones, de hasta 1024 bytes cada una."; return
        }
        guard canChangeSession, let selected, sessionCore.scope == selected.scope,
              let snapshot = sessionCore.snapshot, snapshot.revision > 0,
              OwnerManagementReview.confirm(title: "Compactar contexto", action: "Compactar este contexto",
                detail: "Se preparará el contexto de «\(selected.title)» en la revisión \(snapshot.revision), conservando el primer mensaje, las decisiones indicadas y las referencias a recibos. Parte del texto puede omitirse del contexto del modelo; el historial original se conserva. No concede permisos ni ejecuta herramientas.",
                isCurrent: { canChangeSession && self.selected?.scope == selected.scope && sessionCore.snapshot?.revision == snapshot.revision }) else { return }
        conversationRefresh?.setScope(nil)
        defer { conversationRefresh?.setScope(self.selected?.scope) }
        await sessionCore.compact(decisions: retained)
        if sessionCore.result != nil {
            do { messages = try await decodeMessages(fetchMessages(selected), scope: selected.scope) }
            catch { report(error) }
        }
    }

    func rewindSession(until: Int, title: String) async {
        guard canChangeSession, let selected, sessionCore.scope == selected.scope,
              let snapshot = sessionCore.snapshot, until > 0, until <= snapshot.revision,
              messages.contains(where: { $0.sequence == until && $0.role != "system" }),
              OwnerManagementReview.confirm(title: "Continuar desde un punto anterior", action: "Crear esta rama",
                detail: "Se creará una conversación nueva desde el punto \(until) de «\(selected.title)». El original y los registros de sus acciones se conservan. No revierte efectos, no copia permisos ni reutiliza el hilo del proveedor.",
                isCurrent: { canChangeSession && self.selected?.scope == selected.scope && sessionCore.snapshot?.revision == snapshot.revision }) else { return }
        conversationRefresh?.setScope(nil)
        defer { conversationRefresh?.setScope(self.selected?.scope) }
        await sessionCore.rewind(until: until, title: title)
        if sessionCore.result != nil {
            do {
                messages = try await decodeMessages(fetchMessages(selected), scope: selected.scope)
                try await refreshSessions()
            } catch { report(error) }
        }
    }

    func openSessionBranch(_ branch: SessionBranchSnapshot) async {
        guard canChangeSession, selected?.id == branch.parentID, selected?.agentID == branch.agentID else { return }
        do {
            if branch.scope.temporary, !sessions.contains(where: { $0.id == branch.scope.sessionID }) {
                let value: RPCValue = .object(["session_id": .string(branch.scope.sessionID),
                    "temporary": .bool(true), "title": .string(branch.title), "archived": .bool(false),
                    "agent_id": .string(branch.agentID), "parent_session_id": .string(branch.parentID)])
                guard let session = ChatSession(value) else { throw RPCFailure.invalidResponse }
                sessions.insert(session, at: 0)
            } else { try await refreshSessions() }
            await selectSession(branch.scope.sessionID)
        } catch { report(error) }
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
