import AegisAudioCore
import SwiftUI

/// What the daemon's `health.status` reports: NVIDIA, integrations and recent failures.
struct JarvisHealthReport: Decodable, Sendable, Equatable {
    struct Nvidia: Decodable, Sendable, Equatable {
        let credential: String
        let calls: Int
        let answered: Int
        let rate_limited: Int
        let failed: Int
        let median_latency_ms: Int?
        let last_success_at: String?
        let last_problem_at: String?
        let cooldown_seconds: Double
    }

    struct Server: Decodable, Sendable, Equatable, Identifiable {
        let server_id: String
        let state: String
        let reason_code: String?
        let available_tools: Int
        var id: String { server_id }
    }

    struct Failure: Decodable, Sendable, Equatable, Identifiable {
        let at: String
        let title: String
        let detail: String?
        var id: String { at + title }
    }

    let window_hours: Int
    let nvidia: Nvidia
    let mcp: [Server]
    let failures: [Failure]
}

/// Claude's and Codex's usage panel, as a health panel: NVIDIA has no quota to show,
/// so this shows whether it answers, how often it is rate limited, which integrations
/// are connected and what failed lately, with a way to reconnect them.
struct JarvisHealthView: View {
    let model: MenuBarModel
    let restart: @MainActor () async throws -> Void
    @State private var report: JarvisHealthReport?
    @State private var loading = false
    @State private var restarting = false
    @State private var message: String?

    var body: some View {
        JarvisSettingsCard(title: "Salud de Jarvis") {
            if let report {
                nvidia(report.nvidia, hours: report.window_hours)
                Divider()
                integrations(report.mcp)
                Divider()
                failures(report.failures)
            } else {
                Text(loading ? "Comprobando…" : "No pude leer el estado. Comprueba que el servicio esté conectado.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            HStack {
                Button("Actualizar", systemImage: "arrow.clockwise") { Task { await load() } }
                    .disabled(loading || restarting)
                Spacer()
                Button(restarting ? "Reconectando…" : "Reconectar integraciones",
                       systemImage: "arrow.triangle.2.circlepath") { Task { await reconnect() } }
                    .disabled(restarting || model.pendingApproval != nil || model.chat.isBusy
                              || model.voiceState.isBusy)
                    .help("Reinicia el servicio de Jarvis para volver a conectar las integraciones; no reinstala nada.")
            }
            if let message {
                Text(message).font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
        .task { await load() }
    }

    private func nvidia(_ nvidia: JarvisHealthReport.Nvidia, hours: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("NVIDIA", systemImage: nvidia.cooldown_seconds > 0 ? "hourglass" : "cpu")
                .font(.system(size: 12, weight: .semibold))
            row("Clave", nvidia.credential == "configured" ? "Configurada" : "No disponible",
                good: nvidia.credential == "configured")
            row("Respuestas (últimas \(hours) h)",
                nvidia.calls == 0 ? "Sin llamadas" : "\(nvidia.answered) de \(nvidia.calls)",
                good: nvidia.calls == 0 || nvidia.answered * 10 >= nvidia.calls * 9)
            if nvidia.rate_limited > 0 {
                row("Límite de frecuencia", "\(nvidia.rate_limited) veces", good: nvidia.rate_limited * 20 < max(nvidia.calls, 1))
            }
            if nvidia.failed > 0 { row("Fallos", "\(nvidia.failed)", good: false) }
            if let latency = nvidia.median_latency_ms {
                row("Tiempo típico de respuesta", String(format: "%.1f s", Double(latency) / 1_000), good: latency < 15_000)
            }
            if nvidia.cooldown_seconds > 0 {
                row("Pausa por límite", String(format: "%.0f s", nvidia.cooldown_seconds), good: false)
            }
            if let last = nvidia.last_success_at { row("Última respuesta", Self.relative(last), good: true) }
        }
    }

    private func integrations(_ servers: [JarvisHealthReport.Server]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Integraciones", systemImage: "puzzlepiece.extension").font(.system(size: 12, weight: .semibold))
            if servers.isEmpty {
                Text("No hay integraciones configuradas.").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            ForEach(servers) { server in
                row(Self.serverName(server.server_id), Self.serverState(server),
                    good: server.state == "active" && server.available_tools > 0)
            }
        }
    }

    private func failures(_ failures: [JarvisHealthReport.Failure]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Fallos recientes", systemImage: "exclamationmark.triangle").font(.system(size: 12, weight: .semibold))
            if failures.isEmpty {
                Text("Ninguno en las últimas 24 horas.").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            ForEach(failures) { failure in
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(Self.relative(failure.at)) · \(failure.title)").font(.system(size: 11))
                    if let detail = failure.detail {
                        Text(detail).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
            }
        }
    }

    private func row(_ title: String, _ value: String, good: Bool) -> some View {
        HStack {
            Circle().fill(good ? Color.green : Color.orange).frame(width: 6, height: 6)
            Text(title).font(.system(size: 12)).foregroundStyle(Color.primary.opacity(0.65))
            Spacer()
            Text(value).font(.system(size: 12, weight: .medium)).textSelection(.enabled)
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        report = await model.healthReport()
    }

    private func reconnect() async {
        restarting = true
        defer { restarting = false }
        do {
            try await restart()
            await model.refreshDaemon()
            message = "Servicio reiniciado; las integraciones se volvieron a conectar."
        } catch {
            message = "No se pudo reiniciar el servicio. Inténtalo de nuevo."
        }
        await load()
    }

    static func serverName(_ id: String) -> String {
        switch id {
        case "google-workspace": "Google Workspace"
        case "home-assistant": "Home Assistant"
        case "playwright": "Navegador"
        default: id.replacingOccurrences(of: "-", with: " ").capitalized
        }
    }

    static func serverState(_ server: JarvisHealthReport.Server) -> String {
        switch server.state {
        case "active":
            server.available_tools > 0 ? "Conectada · \(server.available_tools) herramientas" : "Sin herramientas disponibles"
        case "disabled": server.reason_code == "settings_change" ? "En pausa por un cambio de ajustes" : "Desactivada"
        case "quarantined": "Desconectada (\(server.reason_code ?? "sin detalle"))"
        default: server.state
        }
    }

    static func relative(_ iso: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = parser.date(from: iso) ?? ISO8601DateFormatter().date(from: iso) else { return iso }
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = Locale(identifier: "es")
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}
