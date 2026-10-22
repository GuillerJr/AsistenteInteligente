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
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { self = date; return }
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: text) else { return nil }
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
                MenuBarIconButton(symbol: "sidebar.left", title: chat.showHistory ? String(localized: "Ocultar historial", bundle: .module) : String(localized: "Mostrar historial", bundle: .module)) {
                    chat.showHistory.toggle()
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(localized: "Dot", bundle: .module)).font(.system(size: 14, weight: .semibold))
                    Text(String(localized: "Lo que Jarvis hace por su cuenta, a toda hora", bundle: .module)).font(.system(size: 11))
                        .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                }
                Spacer()
                if let overview {
                    Button(overview.paused ? String(localized: "Reanudar Dot", bundle: .module) : String(localized: "Pausar Dot", bundle: .module),
                           systemImage: overview.paused ? "play.fill" : "pause.fill") {
                        Task { await load(overview.paused ? "dot.resume" : "dot.pause") }
                    }
                    .buttonStyle(JarvisButtonStyle(prominent: overview.paused, compact: true))
                    .help(overview.paused ? String(localized: "Las tareas programadas y los objetivos vuelven a correr solos", bundle: .module)
                          : String(localized: "Las tareas programadas y los objetivos dejan de correr solos hasta que lo reanudes", bundle: .module))
                }
            }.padding(.horizontal, 14).frame(height: 52)
            JarvisSeparator()
            ScrollView {
                if let overview { content(overview) }
                else {
                    VStack(spacing: 10) {
                        if failed { Text(String(localized: "No pude leer el estado de Dot.", bundle: .module)).foregroundStyle(JarvisTheme.inkSecondary) }
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
                    Text(overview.paused ? String(localized: "Dot está en pausa", bundle: .module) : overview.now.isEmpty ? String(localized: "Dot está atento", bundle: .module) : String(localized: "Dot está trabajando", bundle: .module))
                        .font(JarvisTheme.voice(24)).foregroundStyle(JarvisTheme.ink)
                    Text(String(localized: "Vigila \(overview.upcoming.count) tareas programadas y \(overview.goals.count) objetivos. Te avisa aquí y por Telegram.", bundle: .module))
                        .font(.system(size: 12.5)).foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            if overview.attention > 0 {
                Button { chat.pane = .chat; chat.showHistory = true } label: {
                    Label(String(localized: "\(overview.attention) \(overview.attention == 1 ? "cosa requiere" : "cosas requieren") tu atención", bundle: .module),
                          systemImage: "bell.badge")
                        .font(.system(size: 13, weight: .medium)).foregroundStyle(JarvisTheme.clay)
                }.buttonStyle(.plain)
            }
            section(String(localized: "Ahora", bundle: .module)) {
                if overview.now.isEmpty { quiet(String(localized: "Nada en curso.", bundle: .module)) }
                ForEach(Array(overview.now.enumerated()), id: \.offset) { _, work in
                    row(work.title, detail: work.detail, symbol: "bolt.fill", conversation: work.conversation_id)
                }
            }
            section(String(localized: "Próximo", bundle: .module)) {
                if overview.upcoming.isEmpty { quiet(String(localized: "Sin tareas programadas. Pídele a Jarvis «cada mañana…».", bundle: .module)) }
                ForEach(overview.upcoming) { task in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Image(systemName: task.monitoring ? "eye" : "clock").frame(width: 16)
                            .foregroundStyle(JarvisTheme.inkSecondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(task.title).font(.system(size: 13, weight: .medium))
                            Text(task.monitoring ? String(localized: "Monitoreando · \(task.schedule)", bundle: .module) : task.schedule)
                                .font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary)
                        }
                        Spacer()
                        if let date = Date(jarvisISO: task.next_run_at) {
                            Text(date, format: .relative(presentation: .named)).font(.system(size: 11.5))
                                .foregroundStyle(JarvisTheme.inkMuted)
                        }
                    }
                }
                Button(String(localized: "Ver tareas programadas", bundle: .module)) { chat.pane = .scheduled }
                    .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(JarvisTheme.clay)
            }
            if !overview.goals.isEmpty {
                section(String(localized: "Objetivos", bundle: .module)) {
                    ForEach(overview.goals) { goal in
                        row(goal.title, detail: String(localized: "\(goal.status_label.capitalized) · \(goal.steps_used) de \(goal.max_steps) pasos", bundle: .module),
                            symbol: "flag", conversation: nil)
                    }
                }
            }
            section(String(localized: "Resultados recientes", bundle: .module)) {
                if overview.recent.isEmpty { quiet(String(localized: "Aún no hay resultados.", bundle: .module)) }
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
