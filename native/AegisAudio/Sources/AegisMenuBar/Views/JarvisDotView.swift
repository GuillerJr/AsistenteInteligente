import SwiftUI

struct JarvisDotOverview: Decodable {
    struct Work: Decodable { let title: String; let detail: String; let conversation_id: String? }
    struct Upcoming: Decodable, Identifiable {
        let task_id: String
        let title: String
        let schedule: String
        let next_run_at: String
        let monitoring: Bool
        var id: String { task_id }
    }
    struct Goal: Decodable, Identifiable {
        let goal_id: String
        let title: String
        let status_label: String
        let steps_used: Int
        let max_steps: Int
        var id: String { goal_id }
    }
    struct Result: Decodable { let title: String; let text: String; let delivered_at: String; let conversation_id: String? }
    let paused: Bool
    let now: [Work]
    let goals: [Goal]
    let upcoming: [Upcoming]
    let recent: [Result]
    let attention: Int
}

extension Date {
    /// The daemon's ISO 8601 timestamps, with or without microseconds.
    init?(jarvisISO text: String) {
        let plain = text.replacingOccurrences(of: #"\.\d+"#, with: "", options: .regularExpression)
        guard let date = ISO8601DateFormatter().date(from: plain) else { return nil }
        self = date
    }
}

/// Dot's page, like Codex's always-on "dot": what Jarvis does on its own around the
/// clock (now, next, results), what waits for the owner, and one switch to pause it.
struct JarvisDotView: View {
    let model: MenuBarModel
    @State private var overview: JarvisDotOverview?
    @State private var failed = false
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                MenuBarIconButton(symbol: "sidebar.left", title: chat.showHistory ? "Ocultar historial" : "Mostrar historial") {
                    chat.showHistory.toggle()
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Dot").font(.system(size: 14, weight: .semibold))
                    Text("Lo que Jarvis hace por su cuenta, a toda hora").font(.system(size: 11))
                        .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                }
                Spacer()
                if let overview {
                    Button(overview.paused ? "Reanudar Dot" : "Pausar Dot",
                           systemImage: overview.paused ? "play.fill" : "pause.fill") {
                        Task { await load(overview.paused ? "dot.resume" : "dot.pause") }
                    }
                    .buttonStyle(JarvisButtonStyle(prominent: overview.paused, compact: true))
                    .help(overview.paused ? "Las tareas programadas y los objetivos vuelven a correr solos"
                          : "Las tareas programadas y los objetivos dejan de correr solos hasta que lo reanudes")
                }
            }.padding(.horizontal, 14).frame(height: 52)
            JarvisSeparator()
            ScrollView {
                if let overview { content(overview) }
                else {
                    VStack(spacing: 10) {
                        if failed { Text("No pude leer el estado de Dot.").foregroundStyle(JarvisTheme.inkSecondary) }
                        else { ProgressView().controlSize(.small) }
                    }.frame(maxWidth: .infinity).padding(.top, 80)
                }
            }
        }
        .frame(minWidth: 480)
        .task {
            while !Task.isCancelled {
                await load("dot.overview")
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
            }
        }
    }

    private func load(_ method: String) async {
        guard let data = await model.performChatIPC(method),
              let value = try? JSONDecoder().decode(JarvisDotOverview.self, from: data) else {
            failed = overview == nil
            return
        }
        overview = value
        failed = false
    }

    private func content(_ overview: JarvisDotOverview) -> some View {
        VStack(alignment: .leading, spacing: 26) {
            HStack(spacing: 14) {
                Circle().fill(overview.paused ? JarvisTheme.inkMuted : JarvisTheme.clay).frame(width: 16, height: 16)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(overview.paused ? "Dot está en pausa" : overview.now.isEmpty ? "Dot está atento" : "Dot está trabajando")
                        .font(JarvisTheme.voice(24)).foregroundStyle(JarvisTheme.ink)
                    Text("Vigila \(overview.upcoming.count) tareas programadas y \(overview.goals.count) objetivos. Te avisa aquí y por Telegram.")
                        .font(.system(size: 12.5)).foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            if overview.attention > 0 {
                Button { chat.pane = .chat; chat.showHistory = true } label: {
                    Label("\(overview.attention) \(overview.attention == 1 ? "cosa requiere" : "cosas requieren") tu atención",
                          systemImage: "bell.badge")
                        .font(.system(size: 13, weight: .medium)).foregroundStyle(JarvisTheme.clay)
                }.buttonStyle(.plain)
            }
            section("Ahora") {
                if overview.now.isEmpty { quiet("Nada en curso.") }
                ForEach(Array(overview.now.enumerated()), id: \.offset) { _, work in
                    row(work.title, detail: work.detail, symbol: "bolt.fill", conversation: work.conversation_id)
                }
            }
            section("Próximo") {
                if overview.upcoming.isEmpty { quiet("Sin tareas programadas. Pídele a Jarvis «cada mañana…».") }
                ForEach(overview.upcoming) { task in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Image(systemName: task.monitoring ? "eye" : "clock").frame(width: 16)
                            .foregroundStyle(JarvisTheme.inkSecondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(task.title).font(.system(size: 13, weight: .medium))
                            Text(task.monitoring ? "Monitoreando · \(task.schedule)" : task.schedule)
                                .font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary)
                        }
                        Spacer()
                        if let date = Date(jarvisISO: task.next_run_at) {
                            Text(date, format: .relative(presentation: .named)).font(.system(size: 11.5))
                                .foregroundStyle(JarvisTheme.inkMuted)
                        }
                    }
                }
                Button("Ver tareas programadas") { chat.pane = .scheduled }
                    .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(JarvisTheme.clay)
            }
            if !overview.goals.isEmpty {
                section("Objetivos") {
                    ForEach(overview.goals) { goal in
                        row(goal.title, detail: "\(goal.status_label.capitalized) · \(goal.steps_used) de \(goal.max_steps) pasos",
                            symbol: "flag", conversation: nil)
                    }
                }
            }
            section("Resultados recientes") {
                if overview.recent.isEmpty { quiet("Aún no hay resultados.") }
                ForEach(Array(overview.recent.enumerated()), id: \.offset) { _, result in
                    row(result.title, detail: result.text, symbol: "tray.full", conversation: result.conversation_id,
                        date: Date(jarvisISO: result.delivered_at))
                }
            }
        }
        .frame(maxWidth: 700, alignment: .leading).frame(maxWidth: .infinity)
        .padding(.horizontal, 24).padding(.vertical, 24)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.system(size: 11, weight: .medium)).foregroundStyle(JarvisTheme.inkMuted)
            VStack(alignment: .leading, spacing: 12, content: content)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous)
                    .strokeBorder(JarvisTheme.hairline))
        }
    }

    private func quiet(_ text: String) -> some View {
        Text(text).font(.system(size: 12.5)).foregroundStyle(JarvisTheme.inkMuted)
    }

    private func row(_ title: String, detail: String, symbol: String, conversation: String?,
                     date: Date? = nil) -> some View {
        Button {
            guard let conversation, let id = UUID(uuidString: conversation) else { return }
            Task {
                await chat.openAttention(.init(id: conversation, title: title, reason: "", action: "",
                                               conversationID: id), model: model)
            }
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Image(systemName: symbol).frame(width: 16).foregroundStyle(JarvisTheme.inkSecondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.system(size: 13, weight: .medium)).foregroundStyle(JarvisTheme.ink)
                    Text(detail).font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary)
                        .lineLimit(3).multilineTextAlignment(.leading)
                }
                Spacer(minLength: 8)
                if let date {
                    Text(date, format: .relative(presentation: .named)).font(.system(size: 11.5))
                        .foregroundStyle(JarvisTheme.inkMuted)
                }
            }.contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(conversation == nil)
    }
}
