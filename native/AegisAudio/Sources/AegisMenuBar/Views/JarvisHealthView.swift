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

/// One line of the health panel, computed in plain Swift so the view stays a single list.
struct JarvisHealthLine: Identifiable, Equatable {
    enum Tone { case good, warning, heading, note, detail }
    let id: Int
    let text: String
    var value = ""
    var tone: Tone
}

extension JarvisHealthReport {
    var lines: [JarvisHealthLine] {
        var lines: [(String, String, JarvisHealthLine.Tone)] = []
        func add(_ text: String, _ value: String = "", _ tone: JarvisHealthLine.Tone) {
            lines.append((text, value, tone))
        }
        let ai = nvidia
        add("NVIDIA", "", .heading)
        add("Clave", ai.credential == "configured" ? "Configurada" : "No disponible",
            ai.credential == "configured" ? .good : .warning)
        add("Respuestas (últimas \(window_hours) h)",
            ai.calls == 0 ? "Sin llamadas" : "\(ai.answered) de \(ai.calls)",
            ai.calls == 0 || ai.answered * 10 >= ai.calls * 9 ? .good : .warning)
        if ai.rate_limited > 0 {
            add("Límite de frecuencia", "\(ai.rate_limited) veces",
                ai.rate_limited * 20 < max(ai.calls, 1) ? .good : .warning)
        }
        if ai.failed > 0 { add("Fallos", "\(ai.failed)", .warning) }
        if let latency = ai.median_latency_ms {
            add("Tiempo típico de respuesta", String(format: "%.1f s", Double(latency) / 1_000),
                latency < 15_000 ? .good : .warning)
        }
        if ai.cooldown_seconds > 0 {
            add("Pausa por límite", String(format: "%.0f s", ai.cooldown_seconds), .warning)
        }
        if let last = ai.last_success_at { add("Última respuesta", Self.relative(last), .good) }
        add("Integraciones", "", .heading)
        if mcp.isEmpty { add("No hay integraciones configuradas.", "", .note) }
        for server in mcp {
            add(Self.serverName(server.server_id), Self.serverState(server),
                server.state == "active" && server.available_tools > 0 ? .good : .warning)
        }
        add("Fallos recientes", "", .heading)
        if failures.isEmpty { add("Ninguno en las últimas 24 horas.", "", .note) }
        for failure in failures {
            add("\(Self.relative(failure.at)) · \(failure.title)", "", .note)
            if let detail = failure.detail { add(detail, "", .detail) }
        }
        return lines.enumerated().map { JarvisHealthLine(id: $0, text: $1.0, value: $1.1, tone: $1.2) }
    }

    static func serverName(_ id: String) -> String {
        switch id {
        case "google-workspace": "Google Workspace"
        case "home-assistant": "Home Assistant"
        case "playwright": "Navegador"
        default: id.replacingOccurrences(of: "-", with: " ").capitalized
        }
    }

    static func serverState(_ server: Server) -> String {
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

/// Claude's and Codex's usage panel, as a health panel: NVIDIA has no quota to show,
/// so this shows whether it answers, how often it is rate limited, which integrations
/// are connected and what failed lately, with a way to reconnect them.
struct JarvisHealthView: View {
    let model: MenuBarModel
    let restart: @MainActor () async throws -> Void
    @State private var lines: [JarvisHealthLine] = []
    @State private var status: String?
    @State private var busy = false

    var body: some View {
        JarvisSettingsCard(title: "Salud de Jarvis") {
            ForEach(lines) { line in
                HStack(spacing: 6) {
                    Circle().fill(line.tone == .good ? JarvisTheme.success : JarvisTheme.warning).frame(width: 6, height: 6)
                        .opacity(line.tone == .good || line.tone == .warning ? 1 : 0)
                    Text(line.text)
                        .font(.system(size: line.tone == .detail ? 10 : 12,
                                      weight: line.tone == .heading ? .semibold : .regular,
                                      design: line.tone == .detail ? .monospaced : .default))
                        .foregroundStyle(line.tone == .heading ? Color.primary : JarvisTheme.inkSecondary)
                    Spacer()
                    Text(line.value).font(.system(size: 12, weight: .medium))
                }
                .padding(.top, line.tone == .heading && line.id > 0 ? 8 : 0)
                .textSelection(.enabled)
            }
            HStack {
                Button("Actualizar", systemImage: "arrow.clockwise") { Task { await load() } }
                Spacer()
                Button("Reconectar integraciones", systemImage: "arrow.triangle.2.circlepath") {
                    Task { await reconnect() }
                }
                .disabled(model.pendingApproval != nil || model.chat.isBusy || model.voiceState.isBusy)
                .help("Reinicia el servicio de Jarvis para volver a conectar las integraciones; no reinstala nada.")
            }
            .disabled(busy)
            if let status { Text(status).font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary) }
        }
        .task { await load() }
    }

    private func load() async {
        busy = true
        defer { busy = false }
        if let report = await model.healthReport() {
            lines = report.lines
        } else {
            lines = []
            status = "No pude leer el estado. Comprueba que el servicio esté conectado."
        }
    }

    private func reconnect() async {
        busy = true
        status = "Reconectando…"
        do {
            try await restart()
            await model.refreshDaemon()
            status = "Servicio reiniciado; las integraciones se volvieron a conectar."
        } catch {
            status = "No se pudo reiniciar el servicio. Inténtalo de nuevo."
        }
        busy = false
        await load()
    }
}
