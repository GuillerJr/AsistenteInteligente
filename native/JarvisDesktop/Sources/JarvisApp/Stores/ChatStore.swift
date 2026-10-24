import AppKit
import DesktopCore
import Foundation
import Observation

struct ChatSession: Identifiable, Hashable {
    let id: String
    let title: String
    let temporary: Bool
    let archived: Bool

    init?(_ value: RPCValue) {
        guard let id = value["session_id"].string, let title = value["title"].string,
              let temporary = value["temporary"].bool else { return nil }
        self.id = id; self.title = title; self.temporary = temporary
        archived = value["archived"].bool ?? false
    }
}

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: String
    let text: String
}

struct RunActivity: Identifiable {
    let id: Int
    let kind: String
}

@MainActor @Observable
final class ChatStore {
    var sessions: [ChatSession] = []
    var selectedID: String?
    var messages: [ChatMessage] = []
    var activities: [RunActivity] = []
    var draft = ""
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

    @ObservationIgnored private var client: LocalRPCClient?
    @ObservationIgnored private let service = ServiceProcess()
    @ObservationIgnored private var monitor: Task<Void, Never>?
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var started = false

    var selected: ChatSession? { sessions.first { $0.id == selectedID } }
    var canSend: Bool { ready && connected && !busy && activeRunID == nil && selected != nil
                       && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var canChangeSession: Bool { ready && !busy && activeRunID == nil }

    func start() async {
        guard !started else { return }
        started = true
        starting = true
        defer { starting = false }
        do {
            let options = try LaunchOptions.parse(Array(CommandLine.arguments.dropFirst()))
            let connection = LocalRPCClient(stateDirectory: options.state)
            client = connection
            try service.start(options)
            try await ServiceBootstrap.waitForHealth(stateDirectory: options.state) { service.isRunning }
            try await refreshStatus()
            try await refreshSessions()
            if sessions.isEmpty { await createSession(temporary: false) }
            else if let first = sessions.first { await selectSession(first.id) }
            monitor = Task { [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .seconds(2)) } catch { return }
                    guard let self else { return }
                    do {
                        guard self.service.isRunning else { throw RPCFailure.disconnected }
                        try await self.refreshStatus()
                    } catch {
                        self.ready = false
                        self.connected = false
                        self.statusText = "Servicio desconectado"
                        self.report(error)
                        return
                    }
                }
            }
        } catch {
            statusText = "Jarvis necesita configuración"
            report(error)
        }
    }

    private func rpc(_ method: String, _ params: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard let client else { throw RPCFailure.configuration }
        return try await client.call(method, params: params)
    }

    private func refreshStatus() async throws {
        let result = try await rpc("service.status")
        guard let serverReady = result["ready"].bool,
              let accountConnected = result["provider"]["connected"].bool else {
            throw RPCFailure.invalidResponse
        }
        ready = serverReady
        connected = accountConnected
        capabilities = result["capabilities"]
        statusText = accountConnected ? "Codex con ChatGPT conectado" : "Inicia sesión con ChatGPT"
    }

    private func refreshSessions() async throws {
        var persistent: [ChatSession] = []
        var offset = 0
        var complete = false
        for _ in 0..<50 {
            let result = try await rpc("sessions.list", ["offset": .number(Double(offset))])
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
        busy = true
        defer { busy = false }
        do {
            let result = try await rpc("sessions.create", ["title": .string(temporary ? "Chat temporal" : "Nueva conversación"),
                                                            "temporary": .bool(temporary)])
            guard let session = ChatSession(result["session"]) else { throw RPCFailure.invalidResponse }
            sessions.insert(session, at: 0)
            selectedID = session.id
            messages = []; activities = []; draft = ""; streamingText = ""; errorMessage = nil
        } catch { report(error) }
    }

    func selectSession(_ id: String) async {
        guard canChangeSession, let session = sessions.first(where: { $0.id == id }) else { return }
        busy = true
        defer { busy = false }
        do {
            let rows = try await fetchMessages(session)
            selectedID = id
            messages = try decodeMessages(rows)
            activities = []; streamingText = ""; errorMessage = nil
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
        errorMessage = nil
        streamingText = ""
        do {
            let result = try await rpc("runs.start", params)
            guard let runID = result["run_id"].string else { throw RPCFailure.invalidResponse }
            activeRunID = runID
            runPhase = result["status"].string ?? "queued"
            draft = ""; activities = []
            // A confirmed run identity starts polling even if refreshing history fails.
            watchRun(runID, session: session)
            try await refreshMessages(session)
        } catch {
            report(error)
            if case RPCFailure.remote = error { return }
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
                    guard let phase = result["run"]["status"].string else {
                        throw RPCFailure.invalidResponse
                    }
                    self.runPhase = phase
                    if ["completed", "failed", "cancelled", "interrupted"].contains(phase) {
                        try await self.refreshMessages(session)
                        if let error = result["run"]["error"].string, !error.isEmpty {
                            self.errorMessage = RunPresentation.errorMessage(error)
                        }
                        self.streamingText = ""
                        self.activeRunID = nil
                        return
                    }
                    try await Task.sleep(for: .milliseconds(350))
                } catch is CancellationError { return }
                catch {
                    self.report(error)
                    // Preserve the run identity: connection failure is not settlement.
                    self.runPhase = "disconnected"
                    return
                }
            }
        }
    }

    func cancel() async {
        guard let runID = activeRunID, let session = selected else { return }
        var params = scope(session); params["run_id"] = .string(runID)
        do { _ = try await rpc("runs.cancel", params); runPhase = "cancelling" }
        catch { report(error) }
    }

    func resumeStatus() {
        guard let runID = activeRunID, let session = selected else { return }
        watchRun(runID, session: session)
    }

    func login() async {
        do {
            let result = try await rpc("provider.login")
            guard let raw = result["url"].string, let url = URL(string: raw), url.scheme == "https",
                  let host = url.host,
                  host == "openai.com" || host.hasSuffix(".openai.com") || host == "chatgpt.com" else {
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
        statusText = "Cerrando el servicio y asentando las ejecuciones…"
        monitor?.cancel(); polling?.cancel()
        await client?.close()
        return await service.stop()
    }

    private func report(_ error: Error) {
        errorMessage = (error as? RPCFailure)?.localizedDescription ?? "No se pudo completar la operación."
    }
}
