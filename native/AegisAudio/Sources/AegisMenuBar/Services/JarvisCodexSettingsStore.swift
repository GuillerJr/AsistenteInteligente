import AegisAudioCore
import Foundation
import Observation

@MainActor @Observable
final class JarvisCodexSettingsStore {
    typealias Transport = @MainActor (String, [String: Any], TimeInterval) async throws -> Data
    private(set) var status: JarvisCodexStatus?
    private(set) var loading = false
    private(set) var busy = false
    private(set) var message: String?
    private(set) var failed = false
    private(set) var loginURL: URL?
    @ObservationIgnored private let transport: Transport?
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var refreshAgain = false

    init(transport: Transport? = nil) { self.transport = transport }

    var shouldPoll: Bool { status?.state == .login_pending || status?.last_test?.state == .running }
    var canConnect: Bool { !busy && status?.installed == true && status?.state == .signed_out }
    var canCancel: Bool { busy || shouldPoll }
    var canChangeModel: Bool { !busy && status?.ready == true && !shouldPoll }
    var canTest: Bool { canChangeModel && status?.hasCompatibleSelection == true }

    func refresh() async {
        guard !loading else { refreshAgain = true; return }
        loading = true
        let id = generation
        defer {
            loading = false
            if refreshAgain {
                refreshAgain = false
                Task { [weak self] in await self?.refresh() }
            }
        }
        do {
            let result = try await request("settings.inference.status")
            guard generation == id, !Task.isCancelled else { return }
            try apply(result)
        } catch {
            guard generation == id, !Task.isCancelled else { return }
            status = nil
            loginURL = nil
            fail(error)
        }
    }

    func monitor() async {
        while shouldPoll && !Task.isCancelled {
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
            await refresh()
        }
    }

    func connect() async {
        guard canConnect else { return }
        await mutate("settings.inference.connect", payload: ["confirm": true])
    }
    func disconnect() async {
        guard canChangeModel else { return }
        await mutate("settings.inference.disconnect", payload: ["confirm": true])
    }
    func saveModel(_ id: String) async {
        guard canChangeModel, status?.compatibleModels.contains(where: { $0.id == id }) == true else { return }
        await mutate("settings.inference.save", payload: ["model_id": id])
    }
    func test() async {
        guard canTest else { return }
        await mutate("settings.inference.test")
    }

    func cancel() async {
        // Cancellation invalidates the outstanding connect/test receipt before suspension.
        await mutate("settings.inference.cancel", cancelling: true)
    }

    private func mutate(_ method: String, payload: [String: Any] = [:], cancelling: Bool = false) async {
        guard cancelling || !busy else { return }
        let id = UUID()
        generation = id
        busy = true
        message = nil
        failed = false
        loginURL = nil
        defer { if generation == id { busy = false } }
        do {
            let result = try await request(method, payload: payload)
            guard generation == id, !Task.isCancelled else { return }
            try apply(result)
            if method == "settings.inference.connect" {
                let object = try JSONSerialization.jsonObject(with: result) as? [String: Any]
                if let raw = object?["authUrl"] as? String {
                    guard let url = JarvisCodexLoginURL.validated(raw) else {
                        throw JarvisCodexSettingsError.invalidResponse
                    }
                    loginURL = url
                }
            }
            if method == "settings.inference.save" {
                message = String(localized: "Modelo guardado. Se usará en la próxima solicitud.", bundle: .module)
            }
        } catch {
            guard generation == id, !Task.isCancelled else { return }
            status = nil
            loginURL = nil
            fail(error)
        }
    }

    private func apply(_ data: Data) throws {
        let value = try JSONDecoder().decode(JarvisCodexStatus.self, from: data)
        try value.validate()
        message = nil
        failed = false
        status = value
        if value.state != .login_pending { loginURL = nil }
        if value.last_test?.state == .passed {
            message = String(localized: "Codex respondió a la prueba de conexión.", bundle: .module)
            failed = false
        } else if value.last_test?.state == .failed {
            fail(JarvisCodexSettingsError.request(value.last_test?.error_code ?? "inference_test_failed"))
        }
    }

    private func fail(_ error: Error) {
        failed = true
        let code: String
        if case JarvisCodexSettingsError.request(let value) = error { code = value }
        else { code = "inference_unavailable" }
        switch code {
        case "inference_not_connected":
            message = String(localized: "Inicia sesión con ChatGPT para continuar.", bundle: .module)
        case "inference_operation_busy":
            message = String(localized: "Termina o cancela la operación pendiente antes de continuar.", bundle: .module)
        case "inference_timeout":
            message = String(localized: "Codex tardó demasiado. Comprueba su estado y vuelve a intentarlo.", bundle: .module)
        case "inference_model_incompatible":
            message = String(localized: "El modelo no admite las capacidades necesarias. Elige otro modelo disponible.", bundle: .module)
        default:
            message = String(localized: "No se pudo completar la conexión con Codex. Actualiza el estado y vuelve a intentarlo.", bundle: .module)
        }
    }

    private func request(_ method: String, payload: [String: Any] = [:]) async throws -> Data {
        if let transport { return try await transport(method, payload, 25) }
        let input = try JSONSerialization.data(withJSONObject: payload)
        return try await Task.detached(priority: .userInitiated) {
            let decoded = try JSONSerialization.jsonObject(with: input) as? [String: Any] ?? [:]
            let client = try LocalIPCClient(secretStore: MacOSIPCSecretStore(), timeoutSeconds: 25)
            let response = try client.call(method: method, payload: decoded)
            guard response.ok else { throw JarvisCodexSettingsError.request(response.errorCode ?? "inference_unavailable") }
            return try JSONSerialization.data(withJSONObject: response.payload)
        }.value
    }
}
