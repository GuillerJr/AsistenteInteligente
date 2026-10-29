import CryptoKit
import Foundation
import Observation

public struct NativeAutomationWork: Sendable {
    public let id: String
    public let nonce: String
    public let digest: String
    public let command: String
    public let arguments: [String: RPCValue]
    public init(_ value: RPCValue) throws {
        guard let object = value.object,
              Set(object.keys) == ["operation_id", "nonce", "digest", "command", "arguments", "payload_json", "remaining_seconds"],
              let id = value["operation_id"].string, ManagementValidation.identifier(id),
              let nonce = value["nonce"].string, ManagementValidation.identifier(nonce),
              let digest = value["digest"].string, ManagementValidation.hash(digest),
              let command = value["command"].string,
              let arguments = value["arguments"].object,
              arguments["operation_id"]?.string == id,
              let payload = value["payload_json"].string, payload.utf8.count <= 131072,
              SHA256.hash(data: Data(payload.utf8)).map({ String(format: "%02x", $0) }).joined() == digest,
              let seconds = value["remaining_seconds"].finiteNumber, seconds > 0,
              seconds <= Self.maximumSeconds(command)
        else { throw RPCFailure.invalidResponse }
        let decoded = try JSONDecoder().decode(RPCValue.self, from: Data(payload.utf8))
        guard decoded.object?.count == 2, decoded["command"].string == command,
              decoded["arguments"] == .object(arguments), Self.commands.contains(command)
        else { throw RPCFailure.invalidResponse }
        self.id = id; self.nonce = nonce; self.digest = digest
        self.command = command; self.arguments = arguments
    }
    public static let commands: Set<String> = ["desktop.resolve", "desktop.windows", "desktop.observe", "desktop.action",
        "browser_open", "browser_attach", "browser_observe", "browser_action", "browser_navigate",
        "browser_history", "browser_close", "browser_search", "browser_download", "media.inspect"]
    public static func maximumSeconds(_ command: String) -> Double { command == "media.inspect" ? 180 : 24 }
    public func params(hostID: String) -> [String: RPCValue] {
        ["host_id": .string(hostID), "operation_id": .string(id), "nonce": .string(nonce), "digest": .string(digest)]
    }
}

private extension RPCValue {
    var finiteNumber: Double? { if case .number(let value) = self, value.isFinite { value } else { nil } }
}

/// Serial native dispatcher with an independent cancellation/heartbeat pump.
/// No model callback reaches these executors without a claim by the sole host
/// authority. A lost claim/result reply is never an instruction to dispatch again.
@MainActor @Observable public final class NativeAutomationHost {
    public private(set) var connected = false
    public private(set) var statusText = "Automatización local sin conectar"
    public private(set) var errorMessage: String?
    public private(set) var desktopStatus: RPCValue = .null
    public private(set) var browserStatus: RPCValue = .null
    public private(set) var permissions: RPCValue = .null
    public var searchTemplate = ""
    @ObservationIgnored public var mediaTranscriber: (@MainActor (Data, String, String, String, String) async throws -> LocalAudioTranscript)?
    public private(set) var testMode = false
    public private(set) var lifecycleBusy = false
    private enum Lifecycle { case starting, stopping, reconnecting }
    @ObservationIgnored private var lifecycleTask: Task<Bool, Never>?
    @ObservationIgnored private var lifecycleKind: Lifecycle?
    @ObservationIgnored private var stopRequested = false
    @ObservationIgnored private var request: MaintenanceClient.Request?
    @ObservationIgnored private var transport: LocalRPCClient?
    @ObservationIgnored private var desktop = MacAutomationExecutor()
    @ObservationIgnored private var browser = BrowserAutomationExecutor()
    @ObservationIgnored private var media = NativeMediaExecutor()
    @ObservationIgnored private var hostID = ""
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var polling: Task<Void, Never>?
    @ObservationIgnored private var active: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var submitted: Set<String> = []
    @ObservationIgnored private var admitted: [String: NativeAutomationWork] = [:]
    @ObservationIgnored private var stopping = false
    @ObservationIgnored private var registered = false
    @ObservationIgnored private var testOrigins: Set<String> = []

    public init() {}

    public func configure(_ transport: LocalRPCClient, testOrigins: Set<String> = []) {
        guard !lifecycleBusy, !connected, !registered, !stopping, polling == nil, active.isEmpty else {
            errorMessage = "Espera al cierre confirmado de las herramientas antes de cambiar su conexión."
            return
        }
        configureIdle(transport, testOrigins: testOrigins)
    }

    private func configureIdle(_ transport: LocalRPCClient, testOrigins: Set<String>) {
        self.transport = transport
        self.testOrigins = testOrigins
        desktop = MacAutomationExecutor(allowOwnTestWindow: !testOrigins.isEmpty)
        request = { try await transport.call($0, params: $1) }
        browser = BrowserAutomationExecutor(testOrigins: testOrigins)
        media = NativeMediaExecutor()
        desktop.beforeEffect = { [weak self] id in
            guard let self else { throw RPCFailure.disconnected }; try await self.checkEffect(id)
        }
        browser.beforeEffect = { [weak self] id in
            guard let self else { throw RPCFailure.disconnected }; try await self.checkEffect(id)
        }
        media.beforeEffect = { [weak self] id in
            guard let self else { throw RPCFailure.disconnected }; try await self.checkEffect(id)
        }
        media.loadPayload = { [weak self] id in
            guard let self else { throw RPCFailure.disconnected }; return try await self.readMediaPayload(id)
        }
        media.transcribe = { [weak self] data, name, mime, digest, locale in
            guard let transcribe = self?.mediaTranscriber else { throw NativeMediaIssue.transcriberUnavailable }
            return try await transcribe(data, name, mime, digest, locale)
        }
        testMode = !testOrigins.isEmpty
    }

    public func start() async {
        if let lifecycleTask { _ = await lifecycleTask.value; return }
        guard !stopping, !stopRequested else { return }
        _ = await transition(.starting) { [self] in
            await startIdle()
            return connected
        }
    }

    private func transition(_ kind: Lifecycle, operation: @escaping @MainActor () async -> Bool) async -> Bool {
        lifecycleBusy = true; lifecycleKind = kind
        let task = Task { @MainActor [self] in
            defer { lifecycleTask = nil; lifecycleKind = nil; lifecycleBusy = false }
            return await operation()
        }
        lifecycleTask = task
        return await task.value
    }

    private func startIdle() async {
        guard !connected, !registered, polling == nil, let request, !stopping else { return }
        generation += 1
        let epoch = generation
        hostID = Self.identifier(); submitted.removeAll()
        do {
            let reply = try await request("automation.attach", ["host_id": .string(hostID),
                "capabilities": .array([.string("desktop"), .string("browser"), .string("media")])])
            guard epoch == generation, reply["attached"].bool == true,
                  reply["host_id"].string == hostID, reply["permits_replay"].bool == false
            else { throw RPCFailure.invalidResponse }
            // A joining stop still owns the valid attach ACK and must detach it.
            registered = true
            guard !stopRequested, !stopping else { return }
            connected = true; statusText = "Mac y navegador conectados · sin acciones automáticas"
            await refresh()
            guard !stopRequested, !stopping else { connected = false; return }
            polling = Task { [weak self] in guard let self else { return }; await self.pump(epoch: epoch) }
        } catch { connected = false; errorMessage = error.localizedDescription }
    }

    private func pump(epoch: Int) async {
        while !Task.isCancelled, connected, !stopping, generation == epoch {
            do {
                guard let request else { return }
                let reply = try await request("automation.poll", ["host_id": .string(hostID)])
                guard reply["host_id"].string == hostID, let values = reply["tasks"].array,
                      values.count <= 8, let cancelled = reply["cancelled"].array,
                      cancelled.count <= 8 else { throw RPCFailure.invalidResponse }
                for id in cancelled.compactMap(\.string) {
                    await desktop.cancel(operationID: id); await browser.cancel(operationID: id); await media.cancel(operationID: id)
                }
                let work = try values.map(NativeAutomationWork.init)
                guard Set(work.map(\.id)).count == work.count else { throw RPCFailure.invalidResponse }
                if active.isEmpty, let item = work.first(where: { !submitted.contains($0.id) }) {
                    guard submitted.count < 8192 else { throw RPCFailure.oversized }
                    submitted.insert(item.id)
                    active[item.id] = Task { [weak self] in guard let self else { return }; await self.dispatch(item, epoch: epoch) }
                }
            } catch {
                connected = false; statusText = "Automatización desconectada · no se repetirán acciones"
                errorMessage = error.localizedDescription
                await desktop.reset(); await browser.reset(); await media.reset()
                break
            }
            do { try await Task.sleep(for: .milliseconds(180)) } catch { break }
        }
        if epoch == generation { polling = nil }
    }

    private func dispatch(_ item: NativeAutomationWork, epoch: Int) async {
        defer { active.removeValue(forKey: item.id); admitted.removeValue(forKey: item.id) }
        guard let request, connected, !stopping, epoch == generation else { return }
        let params = item.params(hostID: hostID)
        let result: RPCValue
        do {
            let permit = try await request("automation.claim", params)
            guard permit["claimed"].bool == true, permit["operation_id"].string == item.id,
                  permit["digest"].string == item.digest, permit["permits_replay"].bool == false,
                  let remaining = permit["remaining_seconds"].finiteNumber, remaining > 0,
                  remaining <= NativeAutomationWork.maximumSeconds(item.command),
                  connected, !stopping, epoch == generation else { throw RPCFailure.invalidResponse }
            admitted[item.id] = item
            if item.command == "media.inspect" {
                result = try await media.execute(item.command, arguments: item.arguments)
            } else if item.command.hasPrefix("desktop.") {
                result = try await desktop.execute(item.command, arguments: item.arguments)
            } else {
                let raw = try await browser.execute(item.command, arguments: item.arguments)
                guard var object = raw.object else { throw RPCFailure.invalidResponse }
                object["schema"] = .string("jarvis.browser.v1")
                object["succeeded"] = .bool(true)
                object["effect_status"] = .string("known")
                if object["verified"] == nil { object["verified"] = .bool(false) }
                result = .object(object)
            }
        } catch {
            // Even a lost claim ACK can have committed in the service. The
            // client did not repeat it and reports only an uncertain receipt.
            result = .object(["succeeded": .bool(false), "verified": .bool(false),
                "effect_status": .string("unknown"),
                "error": .string("native_operation_unconfirmed")])
            errorMessage = error.localizedDescription
        }
        guard epoch == generation else { return }
        do {
            guard let object = result.object, object["succeeded"]?.bool != nil,
                  object["verified"]?.bool != nil,
                  ["known", "unknown"].contains(object["effect_status"]?.string ?? ""),
                  try JSONEncoder().encode(result).count <= 131072 else { throw RPCFailure.invalidResponse }
            var complete = params; complete["result"] = result
            let receipt = try await request("automation.complete", complete)
            guard receipt["accepted"].bool == true, receipt["operation_id"].string == item.id,
                  receipt["permits_replay"].bool == false else { throw RPCFailure.invalidResponse }
        } catch { errorMessage = "El recibo nativo quedó sin confirmar. No se repetirá la acción." }
        await refreshLocalStatus()
    }

    public func refreshLocalStatus() async {
        desktopStatus = (try? await desktop.execute("desktop.status", arguments: [:])) ?? .null
        browserStatus = (try? await browser.execute("browser_status", arguments: [:])) ?? .null
    }
    public func reconnect() async {
        if let lifecycleTask { _ = await lifecycleTask.value; return }
        guard !stopping, !stopRequested, transport != nil else { return }
        _ = await transition(.reconnecting) { [self] in
            guard await stopIdle(handshake: true), !stopRequested, let transport else { return false }
            configureIdle(transport, testOrigins: testOrigins)
            await startIdle()
            return connected
        }
    }
    private func checkEffect(_ id: String) async throws {
        guard connected, !stopping, let item = admitted[id], let request else { throw RPCFailure.disconnected }
        let result = try await request("automation.check", item.params(hostID: hostID))
        guard result["checked"].bool == true, result["operation_id"].string == id,
              result["digest"].string == item.digest, result["permits_replay"].bool == false,
              connected, !stopping else { throw RPCFailure.invalidResponse }
    }
    private func readMediaPayload(_ id: String) async throws -> Data {
        guard connected, !stopping, let item = admitted[id], item.command == "media.inspect", let request else {
            throw RPCFailure.disconnected
        }
        let source = try NativeMediaRequest(item.arguments)
        var payload = Data(), done = false
        while !done {
            try Task.checkCancellation(); try await checkEffect(id)
            guard connected, !stopping, admitted[id]?.digest == item.digest else { throw RPCFailure.disconnected }
            var params = item.params(hostID: hostID); params["offset"] = .number(Double(payload.count))
            let reply = try await request("media.worker.read", params)
            guard let object = reply.object,
                  Set(object.keys) == ["operation_id", "offset", "next_offset", "bytes", "sha256", "data_base64", "complete"],
                  reply["operation_id"].string == id, reply["offset"].integer == payload.count,
                  reply["bytes"].integer == source.byteLength, reply["sha256"].string == source.sourceSHA256,
                  let encoded = reply["data_base64"].string, encoded.utf8.count <= 174764,
                  let block = Data(base64Encoded: encoded), !block.isEmpty, block.count <= 131072,
                  payload.count + block.count <= source.byteLength,
                  reply["next_offset"].integer == payload.count + block.count,
                  let complete = reply["complete"].bool,
                  complete == (payload.count + block.count == source.byteLength) else { throw RPCFailure.invalidResponse }
            payload.append(block); done = complete
        }
        try await checkEffect(id); try source.validate(payload)
        return payload
    }
    public func refresh() async {
        guard let request, connected else { return }
        do {
            permissions = try await request("automation.permissions.list", [:])
            guard permissions["revision"].integer != nil, permissions["rules"].array != nil,
                  permissions["permissions_portable"].bool == false else { throw RPCFailure.invalidResponse }
            searchTemplate = permissions["search_template"].string ?? ""
            await refreshLocalStatus()
        } catch { errorMessage = error.localizedDescription }
    }
    public func saveSearch() async {
        guard !lifecycleBusy, let request, let revision = permissions["revision"].integer else { return }
        do {
            let result = try await request("automation.search.set", ["template": searchTemplate.isEmpty ? .null : .string(searchTemplate),
                "expected_revision": .number(Double(revision))])
            guard result["revision"].integer == revision + 1 else { throw RPCFailure.invalidResponse }
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }
    public func revoke(_ id: String) async {
        guard !lifecycleBusy, let request, let revision = permissions["revision"].integer else { return }
        do {
            var params: [String: RPCValue] = ["id": .string(id), "expected_revision": .number(Double(revision))]
            if let sessionRevision = permissions["session_revision"].integer {
                params["expected_session_revision"] = .number(Double(sessionRevision))
            }
            let result = try await request("automation.permissions.revoke", params)
            guard result["revoked"].bool == true else { throw RPCFailure.invalidResponse }
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }
    public func stop(handshake: Bool = true) async -> Bool {
        if let lifecycleTask {
            if lifecycleKind == .stopping { return await lifecycleTask.value }
            // Stop wins over a start/reconnect already awaiting registration.
            stopRequested = true; stopping = true
            _ = await lifecycleTask.value
            return await stop(handshake: handshake)
        }
        stopRequested = true
        return await transition(.stopping) { [self] in
            defer { stopRequested = false; stopping = false }
            return await stopIdle(handshake: handshake)
        }
    }

    private func stopIdle(handshake: Bool) async -> Bool {
        stopping = true; polling?.cancel(); await polling?.value; polling = nil
        statusText = "Cerrando herramientas y esperando su limpieza real…"
        for id in Array(active.keys) { await desktop.cancel(operationID: id); await browser.cancel(operationID: id); await media.cancel(operationID: id) }
        await desktop.reset(); await browser.reset(); await media.reset()
        for task in Array(active.values) { await task.value }
        var confirmed = true
        if handshake, registered, let request {
            do {
                let value = try await request("automation.detach", ["host_id": .string(hostID), "cleanup_confirmed": .bool(true)])
                confirmed = value["detached"].bool == true && value["permits_replay"].bool == false
            } catch { confirmed = false; errorMessage = error.localizedDescription }
        }
        generation += 1; connected = false
        if confirmed || !handshake { registered = false }
        stopping = stopRequested
        await transport?.close(); statusText = confirmed ? "Automatización local cerrada" : "Cierre nativo sin confirmar"
        return confirmed
    }
    private static func identifier() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
}
